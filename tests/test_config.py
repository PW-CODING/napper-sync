from __future__ import annotations

from napper_sync import config as cfg


def test_check_writable_leaves_an_existing_file_as_it_is(home, tmp_path):
    path = tmp_path / "secret"
    path.write_bytes(b"keep me\n")

    cfg.check_writable(path)

    assert path.read_bytes() == b"keep me\n"


def test_check_writable_leaves_nothing_behind_for_a_new_file(home, tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()

    cfg.check_writable(directory / "secret")

    assert list(directory.iterdir()) == []
