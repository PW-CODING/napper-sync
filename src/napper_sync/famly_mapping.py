"""Translate Famly calendar events into Napper entries.

Each event carries an ``embed`` object that tells its kind::

    embed.type = "CHECK_IN" / "CHECK_OUT"   arrival and pick-up
    embed.type = "childAction"              with actionType = DIAPERCHANGE
    embed.type = "mealRegistration"         a meal; which one is in the title
    no embed.type, but "to" is set          sleep (start and end)

Check-in and check-out have no counterpart in Napper and are skipped.
Everything else maps::

    sleep          -> NAP
    diaper change  -> CHANGED_DIAPER   (diaperContent from diaperingType)
    meal           -> SOLIDS           (meal name into the comment)

Every imported entry gets a marker appended to its comment (``(Famly)`` by
default) so it is obvious in the Napper app what came from the nursery.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from . import config as cfg
from .sync import Entry, parse_time

# Famly happens to use the same words as Napper. The mapping is still spelled
# out so that a new value shows up as skipped instead of slipping through.
DIAPER = {"WET": "WET", "DIRTY": "DIRTY", "MIXED": "MIXED", "DRY": "DRY"}

# Namespace for the deterministic Napper IDs below. Changing it (or the key
# format) would give every Famly event a new ID and re-import everything.
NAMESPACE = uuid.UUID("1e14bada-ee6b-4015-baa9-70ea4e9365f8")


def famly_log_id(child_id: str, category: str, start: datetime) -> str:
    """Stable Napper ID for a Famly event.

    The same event yields the same ID on every run, so a later write for a
    corrected event *overwrites* the entry instead of duplicating it. The
    start goes in as UTC seconds, so the way Famly writes the offset does
    not matter.
    """
    key = f"{child_id}|{category}|{start.astimezone(UTC):%Y-%m-%dT%H:%M:%S}"
    return f"LOG_{uuid.uuid5(NAMESPACE, key)}_{category}"


def translate(
    events: list[dict], marker: str = cfg.DEFAULT_MARKER, child_id: str | None = None
) -> tuple[list[Entry], list[str]]:
    """Famly events -> Napper entries, plus a list of notes about anything
    that was *not* mapped and why. Nothing disappears silently.

    With ``child_id`` every entry gets a deterministic ID (see
    :func:`famly_log_id`); without it the entries get random IDs on write.
    """
    entries: list[Entry] = []
    notes: list[str] = []

    for ev in events:
        embed = ev.get("embed") or {}
        kind = embed.get("type")
        title = str(ev.get("title") or "")

        if kind in ("CHECK_IN", "CHECK_OUT"):
            continue

        try:
            if kind == "childAction" and embed.get("actionType") == "DIAPERCHANGE":
                entry = _diaper(ev, embed, marker)
            elif kind == "mealRegistration":
                entry = _meal(ev, title, marker)
            elif ev.get("to"):
                entry = _sleep(ev, embed, marker)
            elif kind is None and "startNotes" in embed:
                # A sleep without an end yet: a later run imports it.
                since = parse_time(ev["from"])
                notes.append(f"nap still running since {since:%H:%M}, imported once it ends")
                continue
            else:
                notes.append(f"skipped ({kind or 'no type'}): {title[:70]}")
                continue
        except (ValueError, KeyError) as exc:
            notes.append(f"error on '{title[:50]}': {exc}")
            continue
        if child_id:
            entry.log_id = famly_log_id(child_id, entry.category, entry.start)
        entries.append(entry)

    return entries, notes


def _sleep(ev: dict, embed: dict, marker: str) -> Entry:
    return Entry(
        start=parse_time(ev["from"]),
        end=parse_time(ev["to"]),
        category="NAP",
        comment=_comment(marker, embed.get("startNotes"), embed.get("endNotes")),
    )


def _diaper(ev: dict, embed: dict, marker: str) -> Entry:
    kind = str(embed.get("diaperingType") or "").upper()
    fields = {"diaperContent": DIAPER[kind]} if kind in DIAPER else {}
    when = embed.get("occuredOn") or ev.get("from")
    return Entry(
        start=parse_time(when),
        category="CHANGED_DIAPER",
        comment=_comment(marker, embed.get("note"), None if kind in DIAPER else kind),
        fields=fields,
    )


def _meal(ev: dict, title: str, marker: str) -> Entry:
    # Title looks like "<child>: Breakfast" - the part after the colon is the meal.
    meal = title.split(":", 1)[1].strip() if ":" in title else title.strip()
    items = [
        i.get("name")
        for i in (ev.get("embed") or {}).get("mealItems") or []
        if isinstance(i, dict) and i.get("name")
    ]
    return Entry(
        start=parse_time(ev["from"]),
        category="SOLIDS",
        comment=_comment(marker, meal, ", ".join(items) or None),
    )


def _comment(marker: str, *parts) -> str:
    filled = [str(p).strip() for p in parts if p and str(p).strip()]
    if marker:
        filled.append(marker)
    return " ".join(filled)
