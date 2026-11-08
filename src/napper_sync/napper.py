"""Client for the private Napper API (api.napper.app).

Standard library only. Authentication works exactly like the app:

1. ``POST /auth/send-otp``       Napper e-mails a one-time code
2. ``POST /auth/email-login``    exchange the code for idToken + refreshToken
3. ``POST /auth/refresh-token``  renew the idToken while the refreshToken lives

Reading: ``/babies``, ``/today/{babyId}/{isoTime}``,
``/logs-between-days/{babyId}/{from}/{to}``.
Writing: ``PUT /logs/{babyId}`` with the complete log object.
Deleting: ``DELETE /logs/{babyId}/{logId}``.

This is an unofficial, undocumented API. Any app update may change it.
See ``docs/napper-api.md`` for everything that is known about it.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

from . import categories
from . import config as cfg

log = logging.getLogger(__name__)

API_BASE_URL = "https://api.napper.app"
API_CLIENT_VERSION = "6.71.0"  # should roughly match a current app version
API_SOURCE = "APP"
API_LANGUAGE = "en"
API_LOCALE = "en-US"
TIMEOUT = 20
REFRESH_MARGIN = timedelta(days=7)

# Napper throttles writes (about 10 per 10 seconds) and answers 429.
RATE_LIMIT_ATTEMPTS = 4
RATE_LIMIT_DEFAULT_WAIT = 10.0
RATE_LIMIT_MAX_WAIT = 60.0  # a longer demand is not waited out: the call fails instead


class NapperError(Exception):
    """Base error for anything the API side throws at us."""


class NapperAuthError(NapperError):
    """Token missing, expired or rejected."""


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


@dataclass
class Tokens:
    account_id: str
    id_token: str
    refresh_token: str
    device_id: str
    id_token_expires_at: int | None = None
    refresh_token_expires_at: int | None = None

    @property
    def expires_soon(self) -> bool:
        if self.id_token_expires_at is None:
            return False
        exp = datetime.fromtimestamp(self.id_token_expires_at, tz=UTC)
        return exp - datetime.now(UTC) <= REFRESH_MARGIN

    def save(self, path: Path | None = None) -> Path:
        path = path or cfg.napper_tokens_path()
        return cfg.write_secret(path, json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path | None = None) -> Tokens | None:
        """The stored tokens, or ``None`` when there are none.

        Keys this version does not know are ignored, so a file written by a
        newer version still loads. A damaged file asks for a new login.
        """
        path = path or cfg.napper_tokens_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in known})
        except (ValueError, TypeError, AttributeError) as err:
            raise NapperAuthError(
                f"The token file {path} is damaged ({err}). Run `napper-sync login <email>` again."
            ) from err


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class NapperClient:
    def __init__(
        self,
        tokens: Tokens | None = None,
        device_id: str | None = None,
        base_url: str | None = None,
        tokens_path: Path | None = None,
    ):
        self._tokens_path = tokens_path
        self._load_error: NapperAuthError | None = None
        if tokens is None:
            try:
                tokens = Tokens.load(tokens_path)
            except NapperAuthError as err:
                # A login replaces the file, so it has to keep working; a call
                # that needs the tokens reports the damage instead.
                self._load_error = err
        self.tokens = tokens
        self.device_id = device_id or (self.tokens.device_id if self.tokens else _new_device_id())
        self.base_url = base_url or API_BASE_URL

    # ---- HTTP -------------------------------------------------------------

    def _headers(self, auth: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Source": API_SOURCE,
            "Version": API_CLIENT_VERSION,
            "Device": self.device_id,
            "Language": API_LANGUAGE,
            "Locale": API_LOCALE,
            "Local-Timestamp": _now_iso(),
        }
        if auth:
            if not self.tokens:
                if self._load_error:
                    raise self._load_error
                raise NapperAuthError("Not logged in - run `napper-sync login <email>` first.")
            headers["Authorization"] = f"Bearer {self.tokens.id_token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        auth: bool = True,
        allow_refresh: bool = True,
        attempt: int = 1,
    ) -> dict[str, Any]:
        if auth and allow_refresh and self.tokens and self.tokens.expires_soon:
            self.refresh()

        data = json.dumps(body).encode() if body is not None else None
        req = Request(
            f"{self.base_url}{path}", data=data, headers=self._headers(auth), method=method
        )
        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except HTTPError as err:
            if err.code in (401, 403) and auth and allow_refresh and self.tokens:
                self.refresh()
                return self._request(method, path, body=body, auth=auth, allow_refresh=False)
            detail = err.read().decode(errors="replace")[:500]
            if err.code == 429 and attempt < RATE_LIMIT_ATTEMPTS:
                wait = _wait_seconds(detail)
                if wait > RATE_LIMIT_MAX_WAIT:
                    raise NapperError(
                        f"HTTP 429 on {method} {path}: Napper asks to wait {wait:.0f}s, "
                        f"longer than the {RATE_LIMIT_MAX_WAIT:.0f}s this client waits. "
                        f"Try again later. ({detail})"
                    ) from err
                log.info(
                    "Napper rate limit - waiting %.0fs (attempt %d/%d)",
                    wait,
                    attempt + 1,
                    RATE_LIMIT_ATTEMPTS,
                )
                time.sleep(wait)
                return self._request(
                    method, path, body=body, auth=auth, allow_refresh=False, attempt=attempt + 1
                )
            if err.code in (401, 403):
                raise NapperAuthError(f"Authentication rejected ({err.code}): {detail}") from err
            raise NapperError(f"HTTP {err.code} on {method} {path}: {detail}") from err
        except URLError as err:
            raise NapperError(f"Connection failed: {err}") from err

        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as err:
            raise NapperError(f"Invalid JSON from {path}") from err

    # ---- Auth -------------------------------------------------------------

    def send_otp(self, email: str) -> None:
        """Ask Napper to e-mail a one-time code."""
        self._request(
            "POST",
            "/auth/send-otp",
            auth=False,
            body={
                "email": email,
                "useDeviceId": False,
                "source": API_SOURCE,
                "language": API_LANGUAGE,
            },
        )

    def email_login(self, email: str, otp: str) -> Tokens:
        """Exchange the code for tokens and persist them."""
        resp = self._request(
            "POST",
            "/auth/email-login",
            auth=False,
            body={
                "email": email,
                "otp": otp,
                "useDeviceId": False,
            },
        )
        self.tokens = _parse_tokens(resp, self.device_id)
        self.tokens.save(self._tokens_path)
        return self.tokens

    def refresh(self) -> Tokens:
        if not self.tokens:
            raise NapperAuthError("No refresh token available.")
        # Napper replaces the refresh token on every refresh. If the new pair
        # cannot be stored afterwards, the stored one is dead and only a new
        # e-mail login helps - so find out first.
        path = self._tokens_path or cfg.napper_tokens_path()
        try:
            cfg.check_writable(path)
        except OSError as err:
            raise NapperError(
                f"Not refreshing the login: {path} cannot be written "
                f"({err.strerror or err}). Napper replaces the refresh token on every "
                "refresh, so the new one would be lost and the stored one would stop "
                "working. Fix the permissions and run again."
            ) from err
        resp = self._request(
            "POST",
            "/auth/refresh-token",
            allow_refresh=False,
            body={
                "idToken": self.tokens.id_token,
                "refreshToken": self.tokens.refresh_token,
            },
        )
        self.tokens = _parse_tokens(resp, self.device_id)
        self.tokens.save(self._tokens_path)
        return self.tokens

    # ---- Reading ----------------------------------------------------------

    def babies(self) -> list[dict]:
        items = self._request("GET", "/babies").get("items", [])
        return [i for i in items if i.get("type") == "BABY"]

    def today(self, baby_id: str, at: datetime | None = None) -> list[dict]:
        ts = quote((at or _local_now()).isoformat(timespec="milliseconds"), safe="")
        resp = self._request("GET", f"/today/{quote(baby_id, safe='')}/{ts}")
        return (resp.get("item") or {}).get("logs", []) or []

    def logs_between(self, baby_id: str, start: str, end: str) -> list[dict]:
        """``start``/``end`` as ``YYYY-MM-DD`` (inclusive)."""
        path = f"/logs-between-days/{quote(baby_id, safe='')}/{start}/{end}"
        return self._request("GET", path).get("items", []) or []

    # ---- Writing ----------------------------------------------------------

    def build_log(
        self,
        baby_id: str,
        category: str,
        start: datetime,
        end: datetime | None = None,
        *,
        fields: dict | None = None,
        comment: str | None = None,
        log_id: str | None = None,
    ) -> dict:
        """Assemble the full log object the API expects, without sending it.

        The server answers 500 (not a validation error) when fields are
        missing, so the object is always sent complete - including
        ``createdAt``/``updatedAt``/``sortKey2``, which the server overwrites.
        """
        cat = categories.get(category)
        extra = categories.validate(cat.name, fields)

        if cat.span:
            is_open = end is None
            end_dt = end
        else:
            is_open = False
            end_dt = start  # point events carry end == start

        if log_id is None:
            core = start.date().isoformat() if cat.id_scheme == "date" else str(uuid.uuid4())
            log_id = f"LOG_{core}_{cat.name}"

        start_iso = start.isoformat(timespec="milliseconds")
        now_utc = _utc_now_iso()

        body: dict[str, Any] = {
            "id": log_id,
            "sortKey": log_id,
            "partitionKey": baby_id,
            "type": "LOG",
            "category": cat.name,
            "start": start_iso,
            "isOpen": is_open,
            "skipped": False,
            "comment": encode_comment(comment or ""),
            "pauses": [],
            "sortKey2": f"LOG_{start_iso}",
            "createdAt": now_utc,
            "updatedAt": now_utc,
        }
        if end_dt is not None:
            body["end"] = end_dt.isoformat(timespec="milliseconds")
        body.update(extra)
        return body

    def create_log(
        self,
        baby_id: str,
        category: str,
        start: datetime,
        end: datetime | None = None,
        *,
        fields: dict | None = None,
        comment: str | None = None,
        log_id: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Create (or, with an existing ``log_id``, overwrite) a log entry.

        Returns ``{"dry_run", "request", "log_id"}`` plus ``"response"`` and
        ``"server_ids"`` after a real write. ``dry_run`` defaults to True.
        """
        body = self.build_log(
            baby_id, category, start, end, fields=fields, comment=comment, log_id=log_id
        )
        return self._put_log(baby_id, body, dry_run)

    def update_log(
        self,
        baby_id: str,
        existing: dict,
        *,
        start: datetime,
        end: datetime | None = None,
        fields: dict | None = None,
        comment: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Overwrite the times, the comment and the given fields of an entry
        that already exists; everything else on it is kept.

        ``existing`` is the log object as read from the API. A PUT with the
        same ``id`` replaces the entry, which is how a corrected source entry
        (a nap that ended later than first logged, say) reaches Napper
        without losing what someone added to the entry in the app.
        """
        cat = categories.get(str(existing.get("category") or ""))
        extra = categories.validate(cat.name, fields)
        start_iso = start.isoformat(timespec="milliseconds")
        now_utc = _utc_now_iso()

        body: dict[str, Any] = dict(existing)
        body.setdefault("type", "LOG")
        body.setdefault("partitionKey", baby_id)
        body.setdefault("sortKey", body["id"])
        body.setdefault("skipped", False)
        body.setdefault("pauses", [])
        body.setdefault("createdAt", now_utc)
        body["start"] = start_iso
        if cat.span and end is None:
            body["isOpen"] = True
            body.pop("end", None)
        else:
            body["isOpen"] = False
            body["end"] = (end if cat.span else start).isoformat(timespec="milliseconds")
        body["comment"] = encode_comment(comment or "")
        body["sortKey2"] = f"LOG_{start_iso}"
        body["updatedAt"] = now_utc
        body.update(extra)
        return self._put_log(baby_id, body, dry_run)

    def _put_log(self, baby_id: str, body: dict, dry_run: bool) -> dict[str, Any]:
        path = f"/logs/{quote(baby_id, safe='')}"
        request = {"method": "PUT", "path": path, "body": body}
        if dry_run:
            return {"dry_run": True, "request": request, "log_id": body["id"]}

        resp = self._request("PUT", path, body=body)
        return {
            "dry_run": False,
            "request": request,
            "response": resp,
            # The response holds every log of that day, not just the new one,
            # so the ID we generated is the one to trust.
            "log_id": body["id"],
            "server_ids": _ids_from(resp),
        }

    def delete_log(self, baby_id: str, log_id: str, *, dry_run: bool = True) -> dict:
        path = f"/logs/{quote(baby_id, safe='')}/{quote(log_id, safe='')}"
        request = {"method": "DELETE", "path": path}
        if dry_run:
            return {"dry_run": True, "request": request}
        return {"dry_run": False, "request": request, "response": self._request("DELETE", path)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def encode_comment(text: str) -> str:
    """Napper stores comments percent-encoded (``Kürbis & Co`` -> ``K%C3%BCrbis%20%26%20Co``)."""
    return quote(text, safe="")


def decode_comment(text: str | None) -> str:
    """Inverse of :func:`encode_comment`, for entries read from the API."""
    return unquote(text or "")


def _ids_from(resp: dict) -> list[str]:
    try:
        return [str(x["id"]) for x in resp["item"]["logs"] if x.get("id")]
    except (KeyError, TypeError):
        return []


def _wait_seconds(text: str) -> float:
    """Parse 'Too many requests - 10 seconds' into a wait time."""
    match = re.search(r"(\d+)\s*second", text)
    return float(match.group(1)) + 1 if match else RATE_LIMIT_DEFAULT_WAIT


def _new_device_id() -> str:
    return str(uuid.uuid4()).upper()


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _local_now().isoformat(timespec="milliseconds")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_tokens(resp: dict, device_id: str) -> Tokens:
    try:
        item = resp["item"]
        id_tok, ref_tok = item["idToken"], item["refreshToken"]
        return Tokens(
            account_id=str(id_tok["payload"]["sub"]),
            id_token=str(id_tok["token"]),
            refresh_token=str(ref_tok["token"]),
            device_id=device_id,
            id_token_expires_at=_maybe_int(id_tok["payload"].get("exp")),
            refresh_token_expires_at=_maybe_int((ref_tok.get("payload") or {}).get("exp")),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise NapperError(f"Unexpected auth response: {json.dumps(resp)[:300]}") from err


def _maybe_int(value: Any) -> int | None:
    return None if value is None else int(value)
