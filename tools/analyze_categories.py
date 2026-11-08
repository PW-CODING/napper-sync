#!/usr/bin/env python3
"""Download a child's complete Napper history and summarise the categories.

Useful when an app update introduces a new category or field: run this,
compare the output with ``napper_sync/categories.py``, and extend it.

    python tools/analyze_categories.py --baby <ID> [--since 2025-01-01] [--dump history.json]
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

from napper_sync import config as cfg
from napper_sync.napper import NapperClient

COMMON = {
    "id",
    "sortKey",
    "sortKey2",
    "partitionKey",
    "type",
    "category",
    "start",
    "end",
    "isOpen",
    "createdAt",
    "updatedAt",
    "createdByUserId",
    "comment",
    "skipped",
    "pauses",
}
DATE_ID = re.compile(r"^LOG_\d{4}-\d{2}-\d{2}_")


def months(start: date, end: date):
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
        yield cur, min(nxt - timedelta(days=1), end)
        cur = nxt


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--baby", help="baby ID (default: config)")
    p.add_argument("--since", default=f"{date.today().year - 1}-01-01", help="YYYY-MM-DD")
    p.add_argument("--dump", help="also write the raw logs to this JSON file")
    args = p.parse_args()

    baby = args.baby or cfg.Config.load().baby_id
    if not baby:
        p.error("no baby ID: pass --baby or set it in the config")

    client = NapperClient()
    logs: list[dict] = []
    for start, end in months(date.fromisoformat(args.since), date.today()):
        chunk = client.logs_between(baby, start.isoformat(), end.isoformat())
        if chunk:
            print(f"  {start:%Y-%m}: {len(chunk)}")
        logs.extend(chunk)
        time.sleep(0.4)
    print(f"\n{len(logs)} logs in total\n")

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            json.dump(logs, fh, ensure_ascii=False)
        print(f"raw data written to {args.dump}\n")

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for item in logs:
        by_cat[item.get("category", "?")].append(item)

    for cat in sorted(by_cat, key=lambda k: -len(by_cat[k])):
        items = by_cat[cat]
        date_ids = sum(1 for x in items if DATE_ID.match(str(x.get("id", ""))))
        spans = sum(1 for x in items if x.get("end") and x["end"] != x.get("start"))
        fields: dict[str, Counter] = defaultdict(Counter)
        for x in items:
            for k, v in x.items():
                if k not in COMMON:
                    fields[k][json.dumps(v, ensure_ascii=False)[:40]] += 1
        print(f"{cat}  n={len(items)}  date-ids={date_ids}  real-spans={spans}")
        for name, values in sorted(fields.items()):
            sample = ", ".join(v for v, _ in values.most_common(5))
            print(f"    {name} ({sum(values.values())}/{len(items)}): {sample}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
