from __future__ import annotations

import json

from napper_sync import config as cfg
from napper_sync import notify
from napper_sync.cli import main


def test_send_posts_embed(mock_api, home):
    assert notify.send(mock_api.webhook, "Title", "Body", notify.GREEN) is True
    payload = mock_api.discord[0]
    assert payload["embeds"][0]["title"] == "Title"
    assert payload["embeds"][0]["color"] == notify.GREEN


def test_missing_webhook_does_not_raise(home, caplog):
    assert notify.send(None, "x", "y") is False
    assert "No Discord webhook" in caplog.text


def test_unreachable_webhook_does_not_raise(home):
    assert notify.send("http://127.0.0.1:9/discord", "x", "y") is False


def test_cli_notify_test_uses_config(mock_api, home, capsys):
    cfg.ensure_home()
    cfg.config_path().write_text(
        f'[notify]\ndiscord_webhook = "{mock_api.webhook}"\n', encoding="utf-8"
    )
    assert main(["notify"]) == 0
    assert capsys.readouterr().out.strip() == "sent"
    assert mock_api.discord[0]["embeds"][0]["title"] == "Test message"


def test_cli_config_init_and_show(home, capsys):
    assert main(["config", "init"]) == 0
    assert cfg.config_path().exists()
    assert main(["config", "init"]) == 1  # refuses to overwrite
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "famly.host      = app.famly.de" in out


def test_cli_add_dry_run_prints_request(mock_api, home, capsys, monkeypatch):
    from napper_sync import cli, napper

    monkeypatch.setattr(napper, "API_BASE_URL", mock_api.base_url)
    cfg.ensure_home()
    cfg.config_path().write_text('[napper]\nbaby_id = "baby-1"\n', encoding="utf-8")
    napper.Tokens("account-1", "id-token", "refresh-token", "TEST-DEVICE").save()

    assert cli.main(["add", "diaper", "2026-09-18T15:30", "--set", "diaperContent=WET"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["request"]["body"]["diaperContent"] == "WET"
    assert not mock_api.logs


def test_cli_rejects_bad_field_cleanly(mock_api, home, capsys, monkeypatch):
    from napper_sync import cli, napper

    monkeypatch.setattr(napper, "API_BASE_URL", mock_api.base_url)
    cfg.ensure_home()
    napper.Tokens("account-1", "id-token", "refresh-token", "TEST-DEVICE").save()

    code = cli.main(
        ["add", "diaper", "2026-09-18T15:30", "--baby", "baby-1", "--set", "diaperContent=PURPLE"]
    )
    assert code == 1
    assert "not recognised" in capsys.readouterr().err


def test_cli_sync_famly_imports_then_corrects(mock_api, home, monkeypatch):
    from datetime import datetime

    from napper_sync import cli, napper

    monkeypatch.setattr(napper, "API_BASE_URL", mock_api.base_url)
    cfg.ensure_home()
    cfg.config_path().write_text(
        '[napper]\nbaby_id = "baby-1"\n\n[famly]\nchild_id = "child-1"\n\n'
        f'[notify]\ndiscord_webhook = "{mock_api.webhook}"\n',
        encoding="utf-8",
    )
    napper.Tokens("account-1", "id-token", "refresh-token", "TEST-DEVICE").save()

    sleep = {
        "title": "10:35 - 11:23: Child slept for 48 minutes.",
        "from": "2026-09-17T08:35:11+00:00",
        "to": "2026-09-17T09:23:33+00:00",
        "embed": {
            "createdBy": None,
            "endedBy": None,
            "timezone": "Europe/Berlin",
            "startNotes": None,
            "endNotes": None,
        },
    }

    class FakeFamly:  # the one external call; everything below it stays real
        events = [sleep]

        def __init__(self, host=None):
            pass

        def calendar(self, child_id, start, end):
            return list(self.events)

    monkeypatch.setattr(cli, "FamlyClient", FakeFamly)

    assert cli.main(["sync-famly", "--live"]) == 0
    assert len(mock_api.logs) == 1
    assert mock_api.discord[-1]["embeds"][0]["title"] == "1 nursery entry imported"

    FakeFamly.events = [dict(sleep, to="2026-09-17T09:40:00+00:00")]
    assert cli.main(["sync-famly", "--live"]) == 0
    assert len(mock_api.logs) == 1
    (log,) = mock_api.logs.values()
    want = datetime.fromisoformat("2026-09-17T09:40:00+00:00").astimezone()
    assert log["end"] == want.isoformat(timespec="milliseconds")
    assert mock_api.discord[-1]["embeds"][0]["title"] == "1 nursery entry corrected"
