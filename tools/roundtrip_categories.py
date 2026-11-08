#!/usr/bin/env python3
"""Round-trip test against the real Napper API: one entry per category.

Creates one test entry per category on a single day far in the past
(default: 400 days ago), reads them back, checks every field, deletes them
again and verifies the day is empty afterwards. Run it after an app update
to see whether the write contract still holds.

    python tools/roundtrip_categories.py --baby <ID> [--days-back 400]
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta

from napper_sync import categories
from napper_sync import config as cfg
from napper_sync.napper import NapperClient, NapperError, decode_comment

SAMPLES = {
    "NAP": ({"howBabySlept": "SWING", "howBabyWoke": "WOKE_UP_NATURALLY"}, ""),
    "BED_TIME": ({"startMinutesAfterMidnight": 0}, ""),
    "WOKE_UP": ({"babyMoodOnWakeUp": 3}, ""),
    "NIGHT_WAKING": ({"babyMoodOnWakeUp": 1}, ""),
    "NURSING": ({"breastLeftMinutes": 420, "breastRightMinutes": 0, "activeBreast": "LEFT"}, ""),
    "BOTTLE": ({"feedingAmount": 120, "bottleFeedingType": "FORMULA"}, ""),
    "SOLIDS": ({"feedingAmount": 90}, "Pumpkin & beef"),
    "PUMPING": ({"amountPumpedLeft": 60, "amountPumpedRight": 40}, ""),
    "CHANGED_DIAPER": ({"diaperContent": "WET"}, ""),
    "TEMPERATURE": ({"temperature": 38.5}, "after the nap"),
    "MEDICINE": ({}, "Vitamin D"),
}


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--baby", help="baby ID (default: config)")
    p.add_argument("--days-back", type=int, default=400)
    p.add_argument("--keep", action="store_true", help="leave the test entries in place")
    args = p.parse_args()
    baby = args.baby or cfg.Config.load().baby_id
    if not baby:
        p.error("no baby ID: pass --baby or set it in the config")

    client = NapperClient()
    client.refresh()
    base = (datetime.now().astimezone() - timedelta(days=args.days_back)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    day = base.date().isoformat()

    if client.logs_between(baby, day, day):
        print(f"Abort: {day} already has entries. Pick another --days-back.")
        return 1
    print(f"Test day: {day}\n")

    created: list[tuple[str, str]] = []
    problems: list[str] = []
    for i, name in enumerate(categories.CATEGORIES):
        cat = categories.CATEGORIES[name]
        fields, comment = SAMPLES[name]
        start = base + timedelta(minutes=30 * i)
        end = start + timedelta(minutes=20) if cat.span else None
        try:
            result = client.create_log(
                baby, name, start, end, fields=fields, comment=comment, dry_run=False
            )
            created.append((name, result["log_id"]))
            print(f"  created   {name:<15} {result['log_id']}")
        except (NapperError, categories.CategoryError) as exc:
            problems.append(f"{name}: {exc}")
            print(f"  ERROR     {name:<15} {exc}")
        time.sleep(0.5)

    time.sleep(2)
    print(f"\n--- read back {day} ---")
    seen = {x["category"]: x for x in client.logs_between(baby, day, day)}
    for name in categories.CATEGORIES:
        item = seen.get(name)
        if not item:
            problems.append(f"{name}: not found after write")
            print(f"  MISSING   {name}")
            continue
        fields, comment = SAMPLES[name]
        diffs = [
            f"{k}: want {v!r}, got {item.get(k)!r}" for k, v in fields.items() if item.get(k) != v
        ]
        if decode_comment(item.get("comment")) != comment:
            diffs.append(f"comment: want {comment!r}, got {decode_comment(item.get('comment'))!r}")
        if diffs:
            problems.extend(f"{name}: {d}" for d in diffs)
            print(f"  MISMATCH  {name}: {'; '.join(diffs)}")
        else:
            print(f"  ok        {name}")

    if args.keep:
        print("\n--keep given - test entries left in place.")
        return 1 if problems else 0

    print("\n--- cleanup ---")
    for name, log_id in created:
        real_id = seen.get(name, {}).get("id", log_id)
        try:
            client.delete_log(baby, real_id, dry_run=False)
            print(f"  deleted   {name}")
        except NapperError as exc:
            problems.append(f"{name}: delete failed - {exc}")
        time.sleep(0.4)

    time.sleep(2)
    left = client.logs_between(baby, day, day)
    print(f"\n{day} afterwards: {len(left)} entries")
    if left:
        problems.append(f"{len(left)} test entries remain")

    print("\n=== result ===")
    if problems:
        print(f"{len(problems)} problem(s):")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("Every category written, read back correctly and removed again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
