"""Shared fixtures: an in-process mock of the Napper API, a Discord webhook
sink, and an isolated state directory.

The mock reproduces the behaviour observed against the real API:
``PUT /logs/{babyId}`` answers with every log of that day, auth endpoints
return the token envelope, and ``429`` can be injected to exercise the
back-off path.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from napper_sync import config as cfg
from napper_sync.napper import NapperClient, Tokens


class MockState:
    def __init__(self) -> None:
        self.logs: dict[str, dict] = {}  # id -> log object
        self.calls: list[tuple[str, str, dict | None]] = []
        self.discord: list[dict] = []
        self.rate_limit_next = 0  # how many PUTs to answer 429 first
        self.rate_limit_seconds = 0  # the wait the 429 message asks for
        self.auth_fail_next = 0  # how many authed GETs to answer 401 first
        self.refreshes = 0
        self.fail_puts_after: int | None = None  # answer 500 once this many PUTs succeeded
        self.puts_ok = 0
        self.ignore_overwrites = False  # a PUT for an existing id changes nothing


def _handler(state: MockState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw) if raw else None

        def _tokens(self):
            exp = int((datetime.now() + timedelta(days=30)).timestamp())
            return {
                "item": {
                    "idToken": {"token": "id-token", "payload": {"sub": "account-1", "exp": exp}},
                    "refreshToken": {"token": "refresh-token", "payload": {"exp": exp}},
                }
            }

        def do_GET(self):
            state.calls.append(("GET", self.path, None))
            if state.auth_fail_next:
                state.auth_fail_next -= 1
                return self._send({"error": "expired"}, 401)
            if self.path == "/babies":
                return self._send(
                    {
                        "items": [
                            {"id": "baby-1", "name": "Test Baby", "type": "BABY", "isOwner": True},
                            {"id": "adult-1", "name": "Parent", "type": "ADULT"},
                        ]
                    }
                )
            if self.path.startswith("/logs-between-days/"):
                _, _, baby, start, end = self.path.split("/")
                items = [
                    log
                    for log in state.logs.values()
                    if log["partitionKey"] == baby and start <= log["start"][:10] <= end
                ]
                return self._send({"items": items})
            if self.path.startswith("/today/"):
                return self._send({"item": {"logs": list(state.logs.values())}})
            return self._send({"error": "not found"}, 404)

        def do_POST(self):
            body = self._body()
            state.calls.append(("POST", self.path, body))
            if self.path == "/auth/send-otp":
                return self._send({})
            if self.path == "/auth/email-login":
                if body.get("otp") != "123456":
                    return self._send({"error": "bad otp"}, 400)
                return self._send(self._tokens())
            if self.path == "/auth/refresh-token":
                state.refreshes += 1
                return self._send(self._tokens())
            if self.path == "/discord":
                state.discord.append(body)
                return self._send({}, 204)
            return self._send({"error": "not found"}, 404)

        def do_PUT(self):
            body = self._body()
            state.calls.append(("PUT", self.path, body))
            if state.rate_limit_next:
                state.rate_limit_next -= 1
                return self._send(
                    {
                        "name": "HttpError",
                        "statusCode": 429,
                        "message": f"Too many requests - {state.rate_limit_seconds} seconds",
                    },
                    429,
                )
            if state.fail_puts_after is not None and state.puts_ok >= state.fail_puts_after:
                return self._send(
                    {"name": "HttpError", "statusCode": 500, "message": "Internal Server Error"},
                    500,
                )
            if not self.path.startswith("/logs/"):
                return self._send({"error": "not found"}, 404)
            if not body or "category" not in body:
                return self._send(
                    {
                        "name": "HttpError",
                        "statusCode": 422,
                        "message": "MISSING_BODY_PROPERTIES: category",
                    },
                    422,
                )
            if not (state.ignore_overwrites and body["id"] in state.logs):
                state.logs[body["id"]] = body
            state.puts_ok += 1
            day = body["start"][:10]
            same_day = [log for log in state.logs.values() if log["start"][:10] == day]
            return self._send({"item": {"logs": same_day}})

        def do_DELETE(self):
            state.calls.append(("DELETE", self.path, None))
            log_id = self.path.rsplit("/", 1)[-1]
            state.logs.pop(log_id, None)
            return self._send({"item": {}})

    return Handler


@pytest.fixture
def mock_api():
    state = MockState()
    server = HTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base_url = f"http://127.0.0.1:{server.server_port}"
    state.webhook = f"{state.base_url}/discord"
    try:
        yield state
    finally:
        server.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated state directory, so tests never touch ~/.config."""
    monkeypatch.setenv("NAPPER_SYNC_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NAPPER_SYNC_DISCORD_WEBHOOK", raising=False)
    # urllib must not route 127.0.0.1 through any proxy the environment sets.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    return cfg.home()


@pytest.fixture
def client(mock_api, home):
    """A logged-in client against the mock."""
    tokens = Tokens(
        account_id="account-1",
        id_token="id-token",
        refresh_token="refresh-token",
        device_id="TEST-DEVICE",
        id_token_expires_at=int((datetime.now() + timedelta(days=30)).timestamp()),
    )
    return NapperClient(tokens=tokens, base_url=mock_api.base_url)
