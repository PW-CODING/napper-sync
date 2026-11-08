"""The Famly client against a refused request. The network call is the one
thing replaced; URL building, error mapping and the message stay real."""

from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

import pytest

from napper_sync import famly


def refuse_with(code: int, body: bytes):
    def fake_urlopen(req, timeout=None):
        raise HTTPError(req.full_url, code, "refused", {}, BytesIO(body))

    return fake_urlopen


def test_rejected_request_names_the_host_as_a_possible_cause(monkeypatch):
    body = b'{"error": "Your login session has expired", "statusCode": 403}'
    monkeypatch.setattr(famly, "urlopen", refuse_with(403, body))
    client = famly.FamlyClient(token="not-a-real-token", host="app.famly.co")

    with pytest.raises(famly.FamlyAuthError) as err:
        client.calendar("child-1", "2026-09-01", "2026-09-02")

    message = str(err.value)
    assert "app.famly.co" in message
    assert "host" in message
    assert "famly.existingLogins" in message
    assert "session has expired" in message  # the server's own words still come through


def test_other_http_errors_are_not_auth_errors(monkeypatch):
    monkeypatch.setattr(famly, "urlopen", refuse_with(500, b"boom"))
    client = famly.FamlyClient(token="not-a-real-token", host="app.famly.de")

    with pytest.raises(famly.FamlyError) as err:
        client.calendar("child-1", "2026-09-01", "2026-09-02")

    assert not isinstance(err.value, famly.FamlyAuthError)
    assert "500" in str(err.value)
