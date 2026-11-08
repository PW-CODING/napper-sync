"""tools/probe_corrections.py runs here against the in-process mock, so the
script cannot rot unnoticed between two app updates. Replaced: the network
endpoint and the pauses. The sync, the client and the ID derivation are real."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from napper_sync import napper

TOOL = Path(__file__).resolve().parents[1] / "tools" / "probe_corrections.py"


@pytest.fixture
def probe(monkeypatch, mock_api, home):
    spec = importlib.util.spec_from_file_location("probe_corrections", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PAUSE", 0)
    monkeypatch.setattr(napper, "API_BASE_URL", mock_api.base_url)
    napper.Tokens("account-1", "id-token", "refresh-token", "TEST-DEVICE").save()
    return module


def calls(mock_api, method):
    return [c for c in mock_api.calls if c[0] == method]


def test_probe_passes_and_leaves_nothing_behind(probe, mock_api):
    assert probe.main(["--baby", "baby-1"]) == 0

    puts = calls(mock_api, "PUT")
    # create, the simulated edit in the app, the correction - and nothing for the unchanged repeat
    assert len(puts) == 3
    assert len(calls(mock_api, "DELETE")) == 1
    assert mock_api.logs == {}

    first_id = puts[0][2]["id"]
    assert first_id.startswith("LOG_") and first_id.endswith("_NAP")
    assert uuid.UUID(first_id[len("LOG_") : -len("_NAP")]).version == 5
    assert {p[2]["id"] for p in puts} == {first_id}


def test_probe_refuses_a_day_that_already_has_entries(probe, mock_api):
    day = (datetime.now().astimezone() - timedelta(days=401)).date().isoformat()
    mock_api.logs["LOG_x_NAP"] = {
        "id": "LOG_x_NAP",
        "partitionKey": "baby-1",
        "category": "NAP",
        "start": f"{day}T10:00:00.000+00:00",
        "end": f"{day}T10:30:00.000+00:00",
    }

    assert probe.main(["--baby", "baby-1"]) == 1

    assert not calls(mock_api, "PUT") and not calls(mock_api, "DELETE")
    assert "LOG_x_NAP" in mock_api.logs


def test_probe_cleans_up_when_a_step_fails(probe, mock_api):
    mock_api.fail_puts_after = 2  # the correction is the third write

    assert probe.main(["--baby", "baby-1"]) == 1

    assert mock_api.logs == {}
    assert len(calls(mock_api, "DELETE")) == 1


def test_probe_keeps_its_entry_on_request(probe, mock_api):
    assert probe.main(["--baby", "baby-1", "--keep"]) == 0

    assert len(mock_api.logs) == 1
    assert not calls(mock_api, "DELETE")


def test_probe_notices_a_server_that_ignores_overwrites(probe, mock_api):
    mock_api.ignore_overwrites = True  # the assumption the whole correction feature rests on

    assert probe.main(["--baby", "baby-1"]) == 1

    assert mock_api.logs == {}, "the test entry is removed even though the check failed"
