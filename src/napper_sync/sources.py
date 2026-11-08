"""File-based sources: CSV and JSON.

Both share one row format - the fixed columns ``start``, ``end``,
``category``, ``comment``, and any further column is taken as a
category-specific field::

    start,end,category,comment,feedingAmount,bottleFeedingType,diaperContent
    2026-09-17T13:10:00,2026-09-17T14:35:00,nap,,,,
    2026-09-17T15:00:00,,bottle,,120,FORMULA,
    2026-09-17T15:30:00,,diaper,,,,WET

Empty cells are ignored. Categories may be given by API name or alias. An
optional ``id`` column (``LOG_<uuid>_<CATEGORY>``) pins the Napper ID, which
makes a repeated import of the same file update instead of duplicate.

A JSON file holds a list of objects with the same keys; extra fields may sit
next to the fixed ones or be grouped under ``"fields"``. That is the hook
for anything else - another tracker, a Home Assistant export, a sensor
script: whatever can write such a list can feed Napper.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

from .sync import Entry, parse_time

FIXED_COLUMNS = {"id", "start", "end", "category", "comment"}


def from_csv(path: Path) -> list[Entry]:
    out: list[Entry] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("start") or "").strip():
                continue
            out.append(_build(row))
    return out


def from_json(path: Path) -> list[Entry]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON source must be a list of entries")
    return [_build(d) for d in data]


def _build(raw: dict) -> Entry:
    fields = dict(raw.get("fields") or {})
    for key, value in raw.items():
        if key in FIXED_COLUMNS or key == "fields":
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        fields[key] = value
    return Entry(
        start=parse_time(raw["start"]),
        end=parse_time(raw["end"]) if str(raw.get("end") or "").strip() else None,
        category=str(raw.get("category") or "NAP"),
        comment=str(raw.get("comment") or "").strip(),
        fields=fields,
        log_id=str(raw.get("id") or "").strip() or None,
    )


SOURCES: dict[str, Callable[[Path], list[Entry]]] = {
    "csv": from_csv,
    "json": from_json,
}


def load(path: Path, fmt: str | None = None) -> list[Entry]:
    """Load a file, picking the parser from ``fmt`` or the file extension."""
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    if fmt not in SOURCES:
        raise ValueError(f"Unknown source format '{fmt}'. Known: {', '.join(SOURCES)}")
    return SOURCES[fmt](path)
