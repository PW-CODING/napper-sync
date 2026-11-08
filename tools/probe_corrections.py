#!/usr/bin/env python3
"""Live check of the correction path against the real Napper API.

Run it after changing how Famly entries are written, or after a Napper app
update. It uses one day far in the past (default: 401 days back), refuses to
touch a day that already has entries, and removes what it created, even when
a step fails.

What it shows against the real server:

1. an entry under the deterministic (uuid5) ID is accepted and reads back
2. a later write for the same Famly event corrects that entry, not duplicates it
3. a field added by hand in the app (simulated) survives the correction
4. a repeated run with unchanged data writes nothing

It never refreshes tokens, so it is safe to run next to another client that
shares the account: give it a token file with a disabled refresh token.

    python tools/probe_corrections.py --baby <ID> [--days-back 401] [--keep]
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime, timedelta

from napper_sync import config as cfg
from napper_sync.categories import CategoryError
from napper_sync.famly_mapping import translate
from napper_sync.napper import NapperClient, NapperError, decode_comment
from napper_sync.sync import parse_time, sync

PAUSE = 1.5  # seconds between steps: lets read-after-write settle, stays clear of the rate limit
CHILD = "probe-child"  # only feeds the ID derivation; never sent anywhere
MARKER = "(Famly)"


def _quiet(*_args) -> None:
    pass


def _event(start: datetime, end: datetime, note: str | None) -> dict:
    """A Famly sleep event, in the shape the calendar endpoint delivers it."""
    return {
        "title": "Child slept.",
        "from": start.astimezone(UTC).isoformat(),
        "to": end.astimezone(UTC).isoformat(),
        "embed": {"timezone": "Europe/Berlin", "startNotes": None, "endNotes": note},
    }


def _find(client: NapperClient, baby: str, day: str, log_id: str) -> dict | None:
    return next((x for x in client.logs_between(baby, day, day) if x.get("id") == log_id), None)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--baby", help="baby ID (default: config)")
    p.add_argument("--days-back", type=int, default=401)
    p.add_argument("--keep", action="store_true", help="leave the test entry in place")
    args = p.parse_args(argv)
    baby = args.baby or cfg.Config.load().baby_id
    if not baby:
        p.error("no baby ID: pass --baby or set it in the config")

    client = NapperClient()
    start = (datetime.now().astimezone() - timedelta(days=args.days_back)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(minutes=40)
    day = start.date().isoformat()

    if client.logs_between(baby, day, day):
        print(f"Abort: {day} already has entries. Pick another --days-back.")
        return 1
    print(f"Test day: {day}\n")

    problems: list[str] = []

    def check(ok: bool, label: str) -> bool:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            problems.append(label)
        return ok

    (entry,), _ = translate([_event(start, end, None)], MARKER, CHILD)
    log_id = entry.log_id
    try:
        # 1. create under the deterministic ID
        result = sync(client, baby, [entry], live=True, report=_quiet)
        check(result.written == 1, "new entry written under the deterministic ID")
        time.sleep(PAUSE)
        stored = _find(client, baby, day, log_id)
        if not check(stored is not None, "entry reads back under that ID"):
            return _finish(client, baby, day, log_id, problems, args.keep, check)

        # 2. someone adds a field in the app
        client.update_log(
            baby,
            stored,
            start=entry.start,
            end=entry.end,
            fields={"howBabySlept": "SWING"},
            comment=decode_comment(stored.get("comment")),
            dry_run=False,
        )
        time.sleep(PAUSE)
        stored = _find(client, baby, day, log_id) or {}
        check(stored.get("howBabySlept") == "SWING", "field added by hand is stored")

        # 3. the nursery corrects the event
        (fixed,), _ = translate(
            [_event(start, end + timedelta(minutes=17), "woke up happy")], MARKER, CHILD
        )
        check(fixed.log_id == log_id, "the corrected event maps to the same ID")
        result = sync(client, baby, [fixed], live=True, report=_quiet)
        check((result.written, result.updated) == (0, 1), "correction updates, does not create")
        time.sleep(PAUSE)
        check(len(client.logs_between(baby, day, day)) == 1, "still exactly one entry that day")
        stored = _find(client, baby, day, log_id) or {}
        check(
            bool(stored.get("end")) and parse_time(stored["end"]) == fixed.end,
            "end time corrected",
        )
        check(decode_comment(stored.get("comment")) == fixed.comment, "comment corrected")
        check(stored.get("howBabySlept") == "SWING", "field added by hand survived")

        # 4. the same data again
        result = sync(client, baby, [fixed], live=True, report=_quiet)
        check(
            (result.written, result.updated, result.skipped) == (0, 0, 1),
            "repeat with unchanged data writes nothing",
        )
    except (NapperError, CategoryError) as exc:
        check(False, f"stopped by an error: {exc}")
    return _finish(client, baby, day, log_id, problems, args.keep, check)


def _finish(client, baby, day, log_id, problems, keep, check) -> int:
    if keep:
        print("\n--keep given - test entry left in place.")
    else:
        print("\n--- cleanup ---")
        try:
            if _find(client, baby, day, log_id):
                client.delete_log(baby, log_id, dry_run=False)
                print("  deleted the test entry")
            time.sleep(PAUSE)
            check(not client.logs_between(baby, day, day), f"{day} is empty again")
        except NapperError as exc:
            check(False, f"cleanup failed: {exc}")

    print("\n=== result ===")
    if problems:
        print(f"{len(problems)} problem(s):")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("Correction path works against the real API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
