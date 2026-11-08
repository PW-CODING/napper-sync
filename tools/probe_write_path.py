#!/usr/bin/env python3
"""Rediscover the Napper write endpoint if an app update moves it.

Step 1 probes candidate paths with a deliberately unusable body. Nothing is
created by a rejected request, so this is safe to run against a real account:

    404 / 403   path does not exist
    405         path exists, wrong method (strong hint)
    400 / 422   path exists, body was wrong (hit)
    2xx         unexpected - check the app immediately

Step 2 (``--shapes``) sends complete bodies modelled on a real log object to
the path found in step 1. This *does* create one test entry - far in the
past - and deletes it again afterwards.

    python tools/probe_write_path.py --baby <ID>
    python tools/probe_write_path.py --baby <ID> --shapes
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from napper_sync import config as cfg
from napper_sync.napper import NapperClient

CANDIDATES = [
    ("POST", "/log/{b}"),
    ("PUT", "/log/{b}"),
    ("POST", "/logs/{b}"),
    ("PUT", "/logs/{b}"),
    ("POST", "/log"),
    ("POST", "/logs"),
    ("PUT", "/log"),
    ("POST", "/baby/{b}/log"),
    ("POST", "/babies/{b}/log"),
    ("POST", "/babies/{b}/logs"),
    ("PUT", "/babies/{b}/logs"),
    ("POST", "/baby/{b}/logs"),
    ("POST", "/today/{b}"),
    ("POST", "/activity/{b}"),
    ("POST", "/activities/{b}"),
    ("POST", "/log/create"),
    ("POST", "/log/{b}/create"),
]
INTERESTING = {400, 405, 409, 415, 422, 500}


def call(client: NapperClient, method: str, path: str, body) -> tuple[int, str]:
    req = Request(
        f"{client.base_url}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers=client._headers(auth=True),
        method=method,
    )
    try:
        with urlopen(req, timeout=25) as resp:
            return resp.status, resp.read().decode(errors="replace")[:600]
    except HTTPError as err:
        return err.code, err.read().decode(errors="replace")[:600]


def probe_paths(client: NapperClient, baby: str) -> list[tuple[str, str, int]]:
    hits = []
    for method, template in CANDIDATES:
        path = template.format(b=baby)
        code, text = call(client, method, path, {"__probe__": True})
        marker = ""
        if 200 <= code < 300:
            marker = "  <== ACCEPTED - check the app now"
        elif code in INTERESTING:
            marker = "  <== interesting"
        print(f"{code:>4}  {method:<5} {path}{marker}")
        if marker:
            hits.append((method, path, code))
            print(f"      {text[:200]}")
        time.sleep(0.6)
    return hits


def probe_shapes(client: NapperClient, baby: str, method: str, path: str) -> None:
    start = (datetime.now().astimezone() - timedelta(days=400)).replace(
        hour=3, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(hours=1)
    full = client.build_log(baby, "NAP", start, end)

    without_server_fields = {
        k: v for k, v in full.items() if k not in ("createdAt", "updatedAt", "sortKey2")
    }
    shapes = [
        ("A: full object as read back", full),
        ("B: without server-side fields", without_server_fields),
        (
            "C: minimal",
            {k: full[k] for k in ("id", "category", "start", "end", "isOpen", "comment", "pauses")},
        ),
        ("D: wrapped in item", {"item": full}),
        ("E: wrapped in items list", {"items": [full]}),
    ]
    created = None
    for name, body in shapes:
        code, text = call(client, method, path, body)
        print(f"{code:>4}  {name}")
        if code >= 400:
            print(f"      {text[:200]}")
        if 200 <= code < 300:
            created = full["id"]
            print("\nThis shape works:")
            print(json.dumps(body, indent=2))
            break
        time.sleep(0.8)

    if not created:
        print("\nNo shape was accepted.")
        return
    day = start.date().isoformat()
    time.sleep(1.5)
    found = [x for x in client.logs_between(baby, day, day) if x.get("id") == created]
    print(f"\nRead back on {day}: {'found' if found else 'NOT found'}")
    result = client.delete_log(baby, created, dry_run=False)
    print(f"Deleted test entry: {result['request']['path']}")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--baby", help="baby ID (default: config)")
    p.add_argument(
        "--shapes",
        action="store_true",
        help="also probe body shapes on the first hit (creates and deletes one entry)",
    )
    args = p.parse_args()
    baby = args.baby or cfg.Config.load().baby_id
    if not baby:
        p.error("no baby ID: pass --baby or set it in the config")

    client = NapperClient()
    client.refresh()
    hits = probe_paths(client, baby)
    if not hits:
        print("\nNothing interesting. The path has a name not in the candidate list.")
        return 1
    if args.shapes:
        method, path, _ = hits[0]
        print(f"\n--- body shapes on {method} {path} ---")
        probe_shapes(client, baby, method, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
