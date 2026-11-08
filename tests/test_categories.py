from __future__ import annotations

import pytest

from napper_sync import categories
from napper_sync.categories import CategoryError


def test_lookup_by_name_alias_and_case():
    assert categories.get("NAP").name == "NAP"
    assert categories.get("nap").name == "NAP"
    assert categories.get("diaper").name == "CHANGED_DIAPER"
    assert categories.get("windel").name == "CHANGED_DIAPER"
    assert categories.get("changed-diaper").name == "CHANGED_DIAPER"
    assert categories.get("Bedtime").name == "BED_TIME"


def test_unknown_category_lists_known_ones():
    with pytest.raises(CategoryError) as err:
        categories.get("nonsense")
    assert "NAP" in str(err.value) and "MEDICINE" in str(err.value)


def test_day_markers_are_date_scheme_everything_else_uuid():
    date_ones = {c.name for c in categories.CATEGORIES.values() if c.id_scheme == "date"}
    assert date_ones == {"BED_TIME", "WOKE_UP"}


def test_span_categories():
    spans = {c.name for c in categories.CATEGORIES.values() if c.span}
    assert spans == {"NAP", "NIGHT_WAKING", "NURSING"}


@pytest.mark.parametrize(
    "cat,fields,expected",
    [
        ("diaper", {"diaperContent": "wet"}, {"diaperContent": "WET"}),
        (
            "bottle",
            {"feedingAmount": "120", "bottleFeedingType": "formula"},
            {"feedingAmount": 120, "bottleFeedingType": "FORMULA"},
        ),
        ("temperature", {"temperature": "38.5"}, {"temperature": 38.5}),
        (
            "nap",
            {"babyMoodOnSleep": "upset, long_time_to_fall_asleep"},
            {"babyMoodOnSleep": ["UPSET", "LONG_TIME_TO_FALL_ASLEEP"]},
        ),
        ("nap", {"babyMoodOnWakeUp": 3}, {"babyMoodOnWakeUp": 3}),
        ("medicine", {}, {}),
    ],
)
def test_validate_coerces_types(cat, fields, expected):
    assert categories.validate(cat, fields) == expected


@pytest.mark.parametrize(
    "cat,fields,fragment",
    [
        ("diaper", {"diaperContent": "PURPLE"}, "not recognised"),
        ("temperature", {"temperature": "55"}, "outside"),
        ("temperature", {"temperature": "warm"}, "not a number"),
        ("medicine", {"feedingAmount": 5}, "has no field"),
        ("nap", {"babyMoodOnWakeUp": 9}, "outside"),
    ],
)
def test_validate_rejects_bad_values(cat, fields, fragment):
    with pytest.raises(CategoryError) as err:
        categories.validate(cat, fields)
    assert fragment in str(err.value)


def test_overview_mentions_every_category():
    text = categories.overview()
    for name in categories.CATEGORIES:
        assert name in text
