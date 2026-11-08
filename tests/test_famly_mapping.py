"""Mapping tests use anonymised copies of real Famly calendar events."""

from __future__ import annotations

from datetime import datetime

from napper_sync.famly_mapping import translate

CHECK_IN = {
    "title": "8:45: Child was checked in to group Sunflowers",
    "from": "2026-09-16T06:45:43+00:00",
    "to": None,
    "embed": {"type": "CHECK_IN", "timezone": "Europe/Berlin", "canEdit": True},
}
BREAKFAST = {
    "title": "Child: Frühstück",
    "from": "2026-09-16T09:04:00+02:00",
    "to": None,
    "embed": {"type": "mealRegistration", "mealItems": [], "childId": "x"},
}
LUNCH_WITH_ITEMS = {
    "title": "Child: Mittagessen",
    "from": "2026-09-18T12:05:00+02:00",
    "to": None,
    "embed": {"type": "mealRegistration", "mealItems": [{"name": "Kartoffelsuppe"}]},
}
DIAPER = {
    "title": "Child: Windel Wechseln - Nass",
    "from": "2026-09-16T08:10:00+00:00",
    "to": None,
    "embed": {
        "diaperingType": "WET",
        "note": None,
        "actionType": "DIAPERCHANGE",
        "type": "childAction",
        "occuredOn": "2026-09-16T08:12:51+00:00",
    },
}
SLEEP = {
    "title": "10:35 - 11:23: Child slept for 48 minutes.",
    "from": "2026-09-17T08:35:11+00:00",
    "to": "2026-09-17T09:23:33+00:00",
    "embed": {
        "createdBy": None,
        "endedBy": None,
        "timezone": "Europe/Berlin",
        "startNotes": None,
        "endNotes": "woke up happy",
    },
}
# Not captured from a live account yet: the finished nap above without its end.
# Replace it with an anonymised real event once one has been seen.
RUNNING_SLEEP = dict(
    SLEEP,
    title="10:35: Child is sleeping.",
    to=None,
    embed=dict(SLEEP["embed"], endNotes=None),
)
UNKNOWN = {
    "title": "Child: Something new",
    "from": "2026-09-16T10:00:00+00:00",
    "to": None,
    "embed": {"type": "photoUpload"},
}


def is_local(dt: datetime) -> bool:
    """Aware, and expressed in the machine's own zone - what ``parse_time`` promises.

    Comparing instants keeps the tests independent of the zone the machine runs in.
    """
    return dt.tzinfo is not None and dt.utcoffset() == dt.astimezone().utcoffset()


def test_check_in_out_are_dropped_silently():
    entries, notes = translate([CHECK_IN])
    assert entries == [] and notes == []


def test_meal_becomes_solids_with_meal_name_in_comment():
    (entry,), notes = translate([BREAKFAST])
    assert entry.category == "SOLIDS"
    assert entry.comment == "Frühstück (Famly)"
    assert entry.start == datetime.fromisoformat(BREAKFAST["from"])
    assert is_local(entry.start) and notes == []


def test_meal_items_are_listed():
    (entry,), _ = translate([LUNCH_WITH_ITEMS])
    assert entry.comment == "Mittagessen Kartoffelsuppe (Famly)"


def test_diaper_maps_content_and_uses_occurred_on():
    (entry,), _ = translate([DIAPER])
    assert entry.category == "CHANGED_DIAPER"
    assert entry.fields == {"diaperContent": "WET"}
    assert entry.start == datetime.fromisoformat(DIAPER["embed"]["occuredOn"])
    assert is_local(entry.start)
    assert entry.comment == "(Famly)"


def test_sleep_becomes_nap_with_local_times_and_notes():
    (entry,), _ = translate([SLEEP])
    assert entry.category == "NAP"
    assert entry.start == datetime.fromisoformat(SLEEP["from"])
    assert entry.end == datetime.fromisoformat(SLEEP["to"])
    assert is_local(entry.start) and is_local(entry.end)
    assert entry.comment == "woke up happy (Famly)"


def test_running_nap_is_reported_as_running_not_as_unknown():
    entries, notes = translate([RUNNING_SLEEP])
    since = datetime.fromisoformat(RUNNING_SLEEP["from"]).astimezone()

    assert entries == []
    assert len(notes) == 1
    assert "still running" in notes[0] and f"{since:%H:%M}" in notes[0]


def test_untyped_event_without_sleep_fields_is_not_taken_for_a_nap():
    untyped = {"title": "Child: Something", "from": "2026-09-16T10:00:00+00:00", "to": None}
    entries, notes = translate([untyped])

    assert entries == []
    assert len(notes) == 1 and "still running" not in notes[0]


def test_unknown_event_is_reported_not_lost():
    entries, notes = translate([UNKNOWN])
    assert entries == []
    assert len(notes) == 1 and "photoUpload" in notes[0]


def test_marker_is_configurable():
    (entry,), _ = translate([BREAKFAST], marker="[nursery]")
    assert entry.comment.endswith("[nursery]")
    (entry,), _ = translate([BREAKFAST], marker="")
    assert entry.comment == "Frühstück"


def test_unknown_diaper_type_lands_in_comment_not_field():
    odd = dict(DIAPER, embed=dict(DIAPER["embed"], diaperingType="SOGGY"))
    (entry,), _ = translate([odd])
    assert entry.fields == {}
    assert "SOGGY" in entry.comment


# --- deterministic IDs ------------------------------------------------------
# Expected values are uuid5("<child>|<category>|<start as UTC seconds>") in the
# napper-sync namespace, derived by hand with the standard library.


def test_entry_id_is_derived_from_child_category_and_start():
    (entry,), _ = translate([SLEEP], child_id="child-1")
    assert entry.log_id == "LOG_bb35e41e-c4e8-512f-b1e3-e5b11c73e982_NAP"


def test_same_events_yield_same_ids_on_every_run():
    first, _ = translate([SLEEP, DIAPER, BREAKFAST], child_id="child-1")
    second, _ = translate([SLEEP, DIAPER, BREAKFAST], child_id="child-1")
    assert [e.log_id for e in first] == [e.log_id for e in second]
    assert len({e.log_id for e in first}) == 3


def test_id_ignores_how_the_offset_is_written():
    as_utc = dict(BREAKFAST, **{"from": "2026-09-16T07:04:00+00:00"})
    (a,), _ = translate([BREAKFAST], child_id="child-1")
    (b,), _ = translate([as_utc], child_id="child-1")
    assert a.log_id == b.log_id == "LOG_bea8caf9-1cc1-529d-9bea-15e1a5c50771_SOLIDS"


def test_id_changes_with_child_and_with_start():
    (base,), _ = translate([SLEEP], child_id="child-1")
    (other_child,), _ = translate([SLEEP], child_id="child-2")
    later = dict(SLEEP, **{"from": "2026-09-17T08:35:12+00:00"})
    (one_second_later,), _ = translate([later], child_id="child-1")
    assert other_child.log_id != base.log_id
    assert one_second_later.log_id != base.log_id


def test_diaper_id_uses_occurred_on_not_from():
    (entry,), _ = translate([DIAPER], child_id="child-1")
    assert entry.log_id == "LOG_e979686c-cd86-57e5-b766-b17e9ab73226_CHANGED_DIAPER"


def test_without_child_id_entries_get_no_id():
    (entry,), _ = translate([SLEEP])
    assert entry.log_id is None
