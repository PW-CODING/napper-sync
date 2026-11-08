"""Short messages to a Discord webhook - so a silent failure does not stay silent.

Two paths lead here:

1. The sync itself reports: on errors, and when it actually wrote something.
   A clean run with nothing new stays quiet.
2. systemd calls ``napper-sync notify --unit <name>`` via ``OnFailure=`` when
   the service crashes, times out or fails to start - exactly the cases in
   which the sync can no longer report by itself.

The webhook URL is a secret (anyone holding it can post to the channel).
It comes from ``config.toml`` or ``NAPPER_SYNC_DISCORD_WEBHOOK``.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import __version__

log = logging.getLogger(__name__)

TIMEOUT = 15

RED = 0xE03131
YELLOW = 0xF08C00
GREEN = 0x2F9E44


def send(webhook: str | None, title: str, text: str, colour: int = RED) -> bool:
    """Post an embed. Failures are logged, never raised - a broken
    notification must not take the sync down with it."""
    if not webhook:
        log.warning("No Discord webhook configured; would have sent: %s - %s", title, text)
        return False

    payload = {
        "username": "napper-sync",
        "embeds": [
            {
                "title": title[:250],
                "description": text[:3900],
                "color": colour,
                "footer": {
                    "text": f"{socket.gethostname()} · {datetime.now().astimezone():%Y-%m-%d %H:%M}"
                },
            }
        ],
    }
    req = Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": f"napper-sync/{__version__}"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except HTTPError as err:
        log.error("Discord answered %s: %s", err.code, err.read().decode(errors="replace")[:200])
    except (URLError, OSError) as err:
        log.error("Discord unreachable: %s", err)
    return False


def journal(unit: str, lines: int = 20) -> str:
    """Last lines of a systemd unit's journal, for the failure message."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return (result.stdout or result.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as err:
        return f"(journal not readable: {err})"


def unit_failed(webhook: str | None, unit: str) -> bool:
    text = journal(unit)
    return send(
        webhook,
        f"Unit failed: {unit}",
        f"The run did not finish cleanly. Last lines:\n```\n{text[-1500:]}\n```",
        RED,
    )
