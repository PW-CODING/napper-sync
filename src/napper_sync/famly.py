"""Client for the internal Famly API (the one the web app uses).

Famly's official API is a paid add-on for the nursery, not something a
parent can switch on. The web app, however, talks to a plain REST + GraphQL
backend with a single auth header::

    x-famly-accesstoken: <token>

The token lives in the logged-in browser under
``localStorage["famly.accessToken"]`` and is stored here in
``~/.config/napper-sync/famly-token``.

Famly runs one instance per country (``app.famly.de``, ``app.famly.co``, ...).
Which one applies to you is shown in the browser under
``localStorage["famly.existingLogins"]`` -> ``baseUrl``.

The one endpoint that matters for syncing::

    GET /api/v2/calendar?type=RANGE&day=<from>&to=<to>&childId=<id>

It returns everything the nursery logs for the child: sleep, meals, diaper
changes, check-in and check-out. See ``docs/famly-api.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import config as cfg

TIMEOUT = 25

# Famly answers 403 to requests without a browser-style User-Agent, so the
# client sends the same header the web app sends.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


class FamlyError(Exception):
    """Request failed."""


class FamlyAuthError(FamlyError):
    """Token missing, expired or rejected."""


def save_token(token: str, path: Path | None = None) -> Path:
    return cfg.write_secret(path or cfg.famly_token_path(), token)


def load_token(path: Path | None = None) -> str:
    path = path or cfg.famly_token_path()
    if not path.exists():
        raise FamlyAuthError(
            f"No Famly token in {path}. Copy localStorage['famly.accessToken'] from a "
            "logged-in browser and run: napper-sync famly token <VALUE>"
        )
    return path.read_text(encoding="utf-8").strip()


class FamlyClient:
    def __init__(
        self, token: str | None = None, host: str | None = None, token_path: Path | None = None
    ):
        self.token = token or load_token(token_path)
        self.host = host or cfg.DEFAULT_FAMLY_HOST

    @property
    def rest_base(self) -> str:
        return f"https://{self.host}/api"

    @property
    def graphql_url(self) -> str:
        return f"https://{self.host}/graphql"

    def _headers(self) -> dict[str, str]:
        return {
            "x-famly-accesstoken": self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    # ---- REST -------------------------------------------------------------

    def get(self, path: str, **params) -> Any:
        url = f"{self.rest_base}/{path.lstrip('/')}"
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        return self._raw("GET", url)

    # ---- GraphQL ----------------------------------------------------------

    def graphql(self, query: str, variables: dict | None = None) -> Any:
        body = {"query": query, "variables": variables or {}}
        answer = self._raw("POST", self.graphql_url, body)
        if isinstance(answer, dict) and answer.get("errors"):
            raise FamlyError(json.dumps(answer["errors"], ensure_ascii=False)[:500])
        return answer.get("data") if isinstance(answer, dict) else answer

    # ---- shared -----------------------------------------------------------

    def _raw(self, method: str, url: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except HTTPError as err:
            text = err.read().decode(errors="replace")[:500]
            if err.code in (401, 403):
                raise FamlyAuthError(
                    f"Famly at {self.host} rejected the request ({err.code}). Either the "
                    "token has expired, or [famly] host is not the instance your account "
                    "lives on - check localStorage['famly.existingLogins'] in a logged-in "
                    f"browser. Server said: {text}"
                ) from err
            raise FamlyError(f"HTTP {err.code} on {method} {url}: {text}") from err
        except URLError as err:
            raise FamlyError(f"Connection failed: {err}") from err
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw.decode(errors="replace")[:1000]}

    # ---- convenience ------------------------------------------------------

    def calendar(self, child_id: str, start: str, end: str) -> list[dict]:
        """The child's daily events - what the nursery logs.

        ``start``/``end`` as ``YYYY-MM-DD``. Returns a flat list of events,
        each enriched with a ``day`` key.
        """
        answer = self.get("/v2/calendar", type="RANGE", day=start, to=end, childId=child_id)
        out: list[dict] = []
        for block in answer if isinstance(answer, list) else []:
            for day in block.get("days", []) or []:
                for event in day.get("events", []) or []:
                    event = dict(event)
                    event["day"] = day.get("day_localdate") or day.get("day")
                    out.append(event)
        return out

    def children(self) -> list[dict]:
        """Children attached to the account (via GraphQL)."""
        data = self.graphql("{ children { results { id name { fullName } } } }")
        results = ((data or {}).get("children") or {}).get("results") or []
        return [{"id": c.get("id"), "name": (c.get("name") or {}).get("fullName")} for c in results]
