"""The sync core: entries, duplicate detection, and the write loop.

A sync run is deliberately boring:

1. a *source* yields :class:`Entry` objects (CSV, JSON, Famly, ...)
2. the entries already in Napper for that period are fetched
3. anything that is already there is skipped
4. the rest is written

Because of step 3 a repeated run is harmless, which is what makes an hourly
timer safe. Writing is opt-in (``live=True``); the default is a dry run.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import categories
from .napper import NapperClient, decode_comment

log = logging.getLogger(__name__)

# Two entries of the same category count as the same if their start times are
# closer than this - or if their spans overlap.
FUZZ = timedelta(minutes=10)


@dataclass
class Entry:
    start: datetime
    end: datetime | None = None
    category: str = "NAP"
    comment: str = ""
    fields: dict = field(default_factory=dict)
    log_id: str | None = None  # a stable ID makes a repeated write an update
    raw: dict | None = field(default=None, repr=False, compare=False)  # as read from Napper

    def __post_init__(self) -> None:
        cat = categories.get(self.category)
        self.category = cat.name
        self.fields = categories.validate(cat.name, self.fields)
        if not cat.span:
            self.end = self.start
        elif self.end and self.end <= self.start:
            raise ValueError(
                f"{self.category}: end is not after start ({self.start} -> {self.end})"
            )

    @property
    def cat(self) -> categories.Category:
        return categories.CATEGORIES[self.category]

    def label(self) -> str:
        text = f"{self.category} {self.start:%Y-%m-%d %H:%M}"
        if self.cat.span:
            text += f" -> {self.end:%H:%M}" if self.end else " (open)"
        if self.fields:
            text += "  " + " ".join(f"{k}={v}" for k, v in self.fields.items())
        if self.comment:
            text += f'  "{self.comment}"'
        return text

    @classmethod
    def from_napper(cls, item: dict) -> Entry:
        """An entry as it exists in Napper.

        Fields are taken as they are, not validated: a value this version
        does not know must not make the entry invisible to duplicate
        detection.
        """
        cat = categories.CATEGORIES[str(item.get("category") or "").upper()]
        entry = cls(
            start=parse_time(item["start"]),
            end=parse_time(item["end"]) if item.get("end") else None,
            category=cat.name,
            comment=decode_comment(item.get("comment")),
            log_id=str(item.get("id") or "") or None,
            raw=item,
        )
        entry.fields = {f.name: item[f.name] for f in cat.fields if f.name in item}
        return entry


@dataclass
class SyncResult:
    written: int
    skipped: int
    new: list[Entry]
    live: bool
    updated: int = 0
    changed: list[tuple[Entry, list[str]]] = field(default_factory=list)

    @property
    def planned(self) -> int:
        """Entries a live run would write: new ones plus corrections."""
        return len(self.new) + len(self.changed)


def parse_time(value) -> datetime:
    """ISO string -> timezone-aware datetime.

    A value without a zone is taken as local time - which is what you want
    for hand-written timestamps in a CSV.
    """
    if isinstance(value, datetime):
        return value.astimezone()
    return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).astimezone()


def existing_entries(
    client: NapperClient, baby_id: str, first: datetime, last: datetime
) -> list[Entry]:
    """Fetch what is already logged in Napper for the period, with a day of
    slack on either side for timezone edges."""
    raw = client.logs_between(
        baby_id,
        (first.date() - timedelta(days=1)).isoformat(),
        (last.date() + timedelta(days=1)).isoformat(),
    )
    out: list[Entry] = []
    for item in raw:
        cat = (item.get("category") or "").upper()
        if cat not in categories.CATEGORIES or not item.get("start"):
            continue
        try:
            out.append(Entry.from_napper(item))
        except (ValueError, KeyError):
            continue  # tolerate malformed historical data
    return out


def is_duplicate(candidate: Entry, existing: Iterable[Entry]) -> bool:
    for other in existing:
        if other.category != candidate.category:
            continue
        # Day markers: one per day, so the same day already counts.
        if candidate.cat.id_scheme == "date":
            if other.start.date() == candidate.start.date():
                return True
            continue
        if abs(other.start - candidate.start) <= FUZZ:
            return True
        if (
            candidate.cat.span
            and candidate.end
            and other.end
            and candidate.start < other.end
            and other.start < candidate.end
        ):
            return True
    return False


def changes(candidate: Entry, other: Entry) -> list[str]:
    """What writing ``candidate`` would change on ``other`` (same ID).

    Only what the source owns is compared: start, end, comment and the
    fields the source sets. Anything else on the Napper entry is left alone.
    An empty list means there is nothing to do.
    """
    out: list[str] = []
    if _ms(candidate.start) != _ms(other.start):
        out.append(f"start {other.start:%H:%M} -> {candidate.start:%H:%M}")
    if candidate.cat.span and _ms(candidate.end) != _ms(other.end):
        out.append(f"end {_hm(other.end)} -> {_hm(candidate.end)}")
    if candidate.comment != other.comment:
        out.append(f"comment {other.comment!r} -> {candidate.comment!r}")
    for name, value in candidate.fields.items():
        if other.fields.get(name) != value:
            out.append(f"{name} {other.fields.get(name)} -> {value}")
    return out


def _ms(dt: datetime | None) -> datetime | None:
    """Napper stores milliseconds; compare at that precision."""
    return dt if dt is None else dt.replace(microsecond=dt.microsecond // 1000 * 1000)


def _hm(dt: datetime | None) -> str:
    return f"{dt:%H:%M}" if dt else "open"


def sync(
    client: NapperClient, baby_id: str, entries: list[Entry], *, live: bool = False, report=print
) -> SyncResult:
    """Write the entries that are not in Napper yet.

    ``report`` receives human-readable progress lines; pass a no-op to keep
    the run quiet.
    """
    if not entries:
        report("Source returned nothing.")
        return SyncResult(0, 0, [], live)

    first = min(e.start for e in entries)
    last = max((e.end or e.start) for e in entries)
    report(f"Source: {len(entries)} entries from {first:%Y-%m-%d} to {last:%Y-%m-%d}")

    existing = existing_entries(client, baby_id, first, last)
    by_id = {e.log_id: e for e in existing if e.log_id}
    report(f"Already in Napper for that period: {len(existing)} logs")

    # Three outcomes per source entry:
    #   same ID already in Napper  -> correction if anything the source owns differs
    #   same category close by     -> duplicate, left alone (older imports, manual entries)
    #   otherwise                  -> new
    # Decided incrementally so that two near-identical entries in the *source*
    # also collapse into one - not only duplicates of what Napper already has.
    new: list[Entry] = []
    corrections: list[tuple[Entry, Entry, list[str]]] = []
    for entry in sorted(entries, key=lambda e: e.start):
        other = by_id.get(entry.log_id) if entry.log_id else None
        if other is not None:
            diff = changes(entry, other) if other.raw is not None else []
            if diff:
                corrections.append((entry, other, diff))
            continue
        if is_duplicate(entry, existing):
            continue
        new.append(entry)
        existing.append(entry)
        if entry.log_id:
            by_id[entry.log_id] = entry
    skipped = len(entries) - len(new) - len(corrections)
    report(f"To write: {len(new)} new, {len(corrections)} to correct  (unchanged: {skipped})")

    written = updated = 0
    for entry in new:
        result = client.create_log(
            baby_id,
            entry.category,
            entry.start,
            entry.end,
            fields=entry.fields,
            comment=entry.comment,
            log_id=entry.log_id,
            dry_run=not live,
        )
        if live:
            report(f"  written: {entry.label()}   id={result['log_id']}")
            written += 1
        else:
            report(f"  [dry-run] {entry.label()}")
    for entry, other, diff in corrections:
        result = client.update_log(
            baby_id,
            other.raw or {},
            start=entry.start,
            end=entry.end,
            fields=entry.fields,
            comment=entry.comment,
            dry_run=not live,
        )
        what = "; ".join(diff)
        if live:
            report(f"  corrected: {entry.label()}   id={result['log_id']}  ({what})")
            updated += 1
        else:
            report(f"  [dry-run] correct {entry.label()}  ({what})")

    if live:
        report(f"\nDone: {written} entries written, {updated} corrected.")
    else:
        report("\nDry run - nothing was written. Pass --live to write.")
    return SyncResult(
        written, skipped, new, live, updated, [(e, diff) for e, _, diff in corrections]
    )
