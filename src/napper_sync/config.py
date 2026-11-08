"""Configuration and on-disk state for napper-sync.

Everything lives in one directory so that it is easy to back up, move to a
server, or wipe:

    ~/.config/napper-sync/
    ├── config.toml          IDs, host, sync window, Discord webhook
    ├── napper-tokens.json   written by `napper-sync login`, refreshed automatically
    └── famly-token          written by `napper-sync famly token <value>`

The directory can be relocated with the ``NAPPER_SYNC_HOME`` environment
variable (handy for tests and for running several children side by side).

Secrets are stored as separate files rather than inside ``config.toml`` so
that the config can be shared or committed to a private dotfiles repo
without dragging credentials along.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FAMLY_HOST = "app.famly.de"
DEFAULT_SYNC_DAYS = 4
DEFAULT_MARKER = "(Famly)"


def home() -> Path:
    """The state directory. Created lazily by whoever writes into it."""
    override = os.environ.get("NAPPER_SYNC_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "napper-sync"


def config_path() -> Path:
    return home() / "config.toml"


def napper_tokens_path() -> Path:
    return home() / "napper-tokens.json"


def famly_token_path() -> Path:
    return home() / "famly-token"


def ensure_home() -> Path:
    path = home()
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):  # Windows has no POSIX permission bits
        path.chmod(0o700)
    return path


def check_writable(path: Path) -> None:
    """Raise ``OSError`` if :func:`write_secret` would fail on ``path``.

    Changes nothing that is already there: an existing file is opened for
    appending (which tests its write permission without truncating it); for a
    new file a throwaway file in the same directory stands in. A full disk is
    not detected.
    """
    ensure_home()
    if path.exists():
        with path.open("ab"):
            pass
    else:
        with tempfile.TemporaryFile(dir=path.parent):
            pass


def write_secret(path: Path, value: str) -> Path:
    """Write a credential file with owner-only permissions."""
    ensure_home()
    path.write_text(value.strip() + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


@dataclass
class Config:
    """Parsed ``config.toml``. Every field has a default, so a missing file
    is not an error - only the commands that need an ID will complain."""

    baby_id: str | None = None
    famly_host: str = DEFAULT_FAMLY_HOST
    famly_child_id: str | None = None
    sync_days: int = DEFAULT_SYNC_DAYS
    marker: str = DEFAULT_MARKER
    discord_webhook: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        if not path.exists():
            return cls()
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        napper = data.get("napper", {}) or {}
        famly = data.get("famly", {}) or {}
        sync = data.get("sync", {}) or {}
        notify = data.get("notify", {}) or {}
        return cls(
            baby_id=_opt_str(napper.get("baby_id")),
            famly_host=_opt_str(famly.get("host")) or DEFAULT_FAMLY_HOST,
            famly_child_id=_opt_str(famly.get("child_id")),
            sync_days=int(sync.get("days", DEFAULT_SYNC_DAYS)),
            marker=str(sync.get("marker", DEFAULT_MARKER)),
            discord_webhook=_opt_str(notify.get("discord_webhook"))
            or _opt_str(os.environ.get("NAPPER_SYNC_DISCORD_WEBHOOK")),
            raw=data,
        )


def _opt_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


EXAMPLE_CONFIG = """\
# napper-sync configuration
# Location: ~/.config/napper-sync/config.toml  (or $NAPPER_SYNC_HOME/config.toml)

[napper]
# From `napper-sync babies`
baby_id = ""

[famly]
# Famly runs one instance per country. Yours is shown in the browser under
# localStorage["famly.existingLogins"] -> baseUrl.  Common: app.famly.de, app.famly.co
host = "app.famly.de"
# From `napper-sync famly children`
child_id = ""

[sync]
# How many days back each run looks. Four covers a weekend from the Monday run.
days = 4
# Appended to the comment of every entry imported from Famly, so you can tell
# them apart in the Napper app. Set to "" to disable.
marker = "(Famly)"

[notify]
# Discord webhook for failures and successful imports. Leave empty to disable.
# Can also be given via the NAPPER_SYNC_DISCORD_WEBHOOK environment variable.
discord_webhook = ""
"""
