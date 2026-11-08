from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

from napper_sync import config as cfg
from napper_sync.napper import (
    NapperAuthError,
    NapperClient,
    NapperError,
    Tokens,
    decode_comment,
    encode_comment,
)

START = datetime.fromisoformat("2026-09-18T13:00:00").astimezone()
END = START + timedelta(minutes=80)


def test_login_flow_persists_tokens(mock_api, home):
    client = NapperClient(device_id="TEST-DEVICE", base_url=mock_api.base_url)
    client.send_otp("parent@example.test")
    tokens = client.email_login("parent@example.test", "123456")

    assert tokens.account_id == "account-1"
    assert cfg.napper_tokens_path().exists()
    assert Tokens.load().refresh_token == "refresh-token"
    # a fresh client picks the stored tokens up
    assert NapperClient(base_url=mock_api.base_url).tokens.account_id == "account-1"


def test_wrong_otp_is_an_error(mock_api, home):
    client = NapperClient(device_id="TEST-DEVICE", base_url=mock_api.base_url)
    with pytest.raises(NapperError):
        client.email_login("parent@example.test", "000000")


def test_unauthenticated_call_explains_itself(mock_api, home):
    with pytest.raises(NapperAuthError, match="login"):
        NapperClient(base_url=mock_api.base_url).babies()


def test_babies_filters_adults(client):
    assert [b["id"] for b in client.babies()] == ["baby-1"]


def test_headers_carry_app_identity(client, mock_api):
    client.babies()
    headers = client._headers()
    assert headers["Source"] == "APP"
    assert headers["Device"] == "TEST-DEVICE"
    assert headers["Authorization"] == "Bearer id-token"


def test_401_triggers_refresh_and_retry(client, mock_api):
    mock_api.auth_fail_next = 1
    assert client.babies()[0]["id"] == "baby-1"
    assert mock_api.refreshes == 1


def test_expiring_token_is_refreshed_before_the_call(mock_api, home):
    soon = int((datetime.now() + timedelta(days=2)).timestamp())
    tokens = Tokens("account-1", "old", "refresh-token", "TEST-DEVICE", id_token_expires_at=soon)
    client = NapperClient(tokens=tokens, base_url=mock_api.base_url)
    client.babies()
    assert mock_api.refreshes == 1
    assert client.tokens.id_token == "id-token"


def test_build_log_point_event_has_end_equal_start(client):
    body = client.build_log("baby-1", "diaper", START, fields={"diaperContent": "WET"})
    assert body["end"] == body["start"]
    assert body["isOpen"] is False
    assert body["diaperContent"] == "WET"
    assert body["id"].startswith("LOG_") and body["id"].endswith("_CHANGED_DIAPER")
    assert body["id"] == body["sortKey"]
    assert body["partitionKey"] == "baby-1"
    for key in ("createdAt", "updatedAt", "sortKey2", "pauses", "skipped", "type"):
        assert key in body, f"server-required field {key} missing"


def test_build_log_span_without_end_is_open(client):
    body = client.build_log("baby-1", "nap", START)
    assert body["isOpen"] is True
    assert "end" not in body


def test_build_log_day_marker_uses_date_id(client):
    body = client.build_log("baby-1", "bedtime", START, fields={"startMinutesAfterMidnight": 0})
    assert body["id"] == "LOG_2026-09-18_BED_TIME"


def test_create_log_is_dry_run_by_default(client, mock_api):
    result = client.create_log("baby-1", "nap", START, END)
    assert result["dry_run"] is True
    assert not [c for c in mock_api.calls if c[0] == "PUT"]


def test_create_log_live_writes_and_reports_own_id(client, mock_api):
    # another log on the same day - the server returns the whole day
    client.create_log(
        "baby-1",
        "diaper",
        START - timedelta(hours=2),
        fields={"diaperContent": "WET"},
        dry_run=False,
    )
    result = client.create_log("baby-1", "nap", START, END, dry_run=False)

    assert result["dry_run"] is False
    assert result["log_id"] == result["request"]["body"]["id"]
    assert len(result["server_ids"]) == 2, "response holds every log of the day"
    assert result["log_id"] in mock_api.logs


def test_rate_limit_is_retried(client, mock_api):
    mock_api.rate_limit_next = 2
    result = client.create_log("baby-1", "nap", START, END, dry_run=False)
    assert result["log_id"] in mock_api.logs
    puts = [c for c in mock_api.calls if c[0] == "PUT"]
    assert len(puts) == 3


def test_delete_log(client, mock_api):
    created = client.create_log("baby-1", "nap", START, END, dry_run=False)
    assert client.delete_log("baby-1", created["log_id"])["dry_run"] is True
    client.delete_log("baby-1", created["log_id"], dry_run=False)
    assert created["log_id"] not in mock_api.logs


def test_comment_round_trip():
    text = "Kürbis & Fleisch 90 g"
    encoded = encode_comment(text)
    assert encoded == "K%C3%BCrbis%20%26%20Fleisch%2090%20g"
    assert decode_comment(encoded) == text
    assert decode_comment(None) == ""


def test_update_log_rewrites_times_and_keeps_fields_added_in_the_app(client, mock_api):
    created = client.create_log("baby-1", "nap", START, END, comment="(Famly)", dry_run=False)
    stored = mock_api.logs[created["log_id"]]
    stored["howBabySlept"] = "SWING"  # the parent added this in the app later
    new_end = END + timedelta(minutes=17)

    result = client.update_log(
        "baby-1", stored, start=START, end=new_end, comment="woke up happy (Famly)", dry_run=False
    )

    after = mock_api.logs[created["log_id"]]
    assert result["log_id"] == created["log_id"]
    assert len(mock_api.logs) == 1
    assert after["end"] == new_end.isoformat(timespec="milliseconds")
    assert after["comment"] == "woke%20up%20happy%20%28Famly%29"
    assert after["howBabySlept"] == "SWING"
    assert after["createdAt"] == stored["createdAt"]
    assert after["sortKey2"] == f"LOG_{START.isoformat(timespec='milliseconds')}"


def test_update_log_is_dry_run_by_default(client, mock_api):
    created = client.create_log("baby-1", "nap", START, END, dry_run=False)
    before = len([c for c in mock_api.calls if c[0] == "PUT"])

    result = client.update_log(
        "baby-1", mock_api.logs[created["log_id"]], start=START, end=END + timedelta(minutes=5)
    )

    assert result["dry_run"] is True
    assert len([c for c in mock_api.calls if c[0] == "PUT"]) == before


# ---- a refresh must not be sent when its result cannot be stored ------------
#
# Napper rotates the refresh token on every refresh: once the request went
# through, the stored token is dead. If the new pair then cannot be written,
# only a new e-mail login helps. So the token file has to be writable *before*
# the request is sent.


def _tokens(*, expiring: bool) -> Tokens:
    days = 2 if expiring else 30
    exp = int((datetime.now() + timedelta(days=days)).timestamp())
    return Tokens("account-1", "old", "refresh-token", "TEST-DEVICE", id_token_expires_at=exp)


@pytest.fixture
def lock_token_file(home):
    """Store tokens in the default file and make it read-only."""
    locked = []

    def lock(tokens: Tokens):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("root ignores file permissions")
        path = tokens.save()
        path.chmod(0o400)
        locked.append(path)
        return path

    yield lock
    for path in locked:
        path.chmod(0o600)


def test_expiring_token_is_not_refreshed_when_the_token_file_is_read_only(
    mock_api, lock_token_file
):
    path = lock_token_file(_tokens(expiring=True))
    before = path.read_bytes()
    client = NapperClient(base_url=mock_api.base_url)

    with pytest.raises(NapperError, match="napper-tokens.json"):
        client.babies()

    assert mock_api.refreshes == 0
    assert path.read_bytes() == before, "the check must not touch the stored tokens"


def test_401_does_not_refresh_when_the_token_file_is_read_only(mock_api, lock_token_file):
    lock_token_file(_tokens(expiring=False))
    mock_api.auth_fail_next = 1
    client = NapperClient(base_url=mock_api.base_url)

    with pytest.raises(NapperError, match="napper-tokens.json"):
        client.babies()

    assert mock_api.refreshes == 0


def test_no_refresh_when_the_token_file_cannot_be_created(mock_api, home, tmp_path):
    not_a_directory = tmp_path / "blocker"
    not_a_directory.write_text("a file where the directory should be")
    client = NapperClient(
        tokens=_tokens(expiring=True),
        base_url=mock_api.base_url,
        tokens_path=not_a_directory / "napper-tokens.json",
    )

    with pytest.raises(NapperError, match="napper-tokens.json"):
        client.babies()

    assert mock_api.refreshes == 0


@pytest.mark.parametrize("file_exists", [False, True], ids=["no file yet", "writable file"])
def test_refresh_stores_the_new_tokens_and_leaves_nothing_else_behind(mock_api, home, file_exists):
    tokens = _tokens(expiring=True)
    if file_exists:
        tokens.save()
    client = NapperClient(tokens=tokens, base_url=mock_api.base_url)

    client.babies()

    assert mock_api.refreshes == 1
    assert Tokens.load().id_token == "id-token"
    assert [p.name for p in home.iterdir()] == ["napper-tokens.json"]
