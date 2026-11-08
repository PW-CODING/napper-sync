"""What Napper expects for each log category.

Derived from several thousand real log entries read back through the API.
Two things set the categories apart:

1. **ID scheme.** ``BED_TIME`` and ``WOKE_UP`` carry the *date* in their ID
   (``LOG_2026-09-14_WOKE_UP``), so there can be at most one per day - a
   second write for the same day overwrites the first. Every other category
   uses a UUID and may occur any number of times per day.

2. **Point vs. span.** A point event (diaper, bottle, temperature, ...) still
   has an ``end`` - it is simply equal to ``start``. Only ``NAP``,
   ``NURSING``, ``NIGHT_WAKING`` and occasionally ``BED_TIME`` have a real
   duration.

The enum values below are the ones observed in real data. The app may know
more; an unknown value is rejected *here*, not by the API. If you are sure a
value is valid, add it to this file.
"""

from __future__ import annotations

from dataclasses import dataclass


class CategoryError(ValueError):
    """Unknown category or a field value that does not fit it."""


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # "enum" | "enum_list" | "int" | "float"
    values: tuple[str, ...] = ()  # for enum / enum_list
    range: tuple | None = None  # for int / float: (min, max)
    note: str = ""


@dataclass(frozen=True)
class Category:
    name: str
    label: str
    id_scheme: str  # "uuid" (any number per day) | "date" (one per day)
    span: bool  # real duration, or a point in time
    fields: tuple[Field, ...] = ()
    note: str = ""

    def field(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


# --- shared value lists -----------------------------------------------------

HOW_SLEPT = ("IN_BED", "NEXT_TO_ME", "NURSING", "WORN_OR_HELD", "SWING", "STROLLER", "CAR")
HOW_WOKE = ("WOKE_UP_NATURALLY", "WOKE_UP_CHILD")
MOOD_ON_SLEEP = ("LONG_TIME_TO_FALL_ASLEEP", "UPSET")

F_HOW_SLEPT = Field("howBabySlept", "enum", HOW_SLEPT, note="how the child fell asleep")
F_HOW_WOKE = Field("howBabyWoke", "enum", HOW_WOKE, note="woke naturally or was woken")
F_MOOD_SLEEP = Field("babyMoodOnSleep", "enum_list", MOOD_ON_SLEEP, note="list, several allowed")
F_MOOD_WAKE = Field("babyMoodOnWakeUp", "int", range=(1, 5), note="mood after waking, 1-5")


# --- the categories ---------------------------------------------------------

CATEGORIES: dict[str, Category] = {
    c.name: c
    for c in [
        # ---- sleep ----
        Category(
            "NAP",
            "Nap",
            "uuid",
            span=True,
            fields=(F_HOW_SLEPT, F_HOW_WOKE, F_MOOD_SLEEP, F_MOOD_WAKE),
            note="Daytime sleep. Without an end time the entry stays open.",
        ),
        Category(
            "BED_TIME",
            "Bedtime",
            "date",
            span=False,
            fields=(
                Field(
                    "startMinutesAfterMidnight",
                    "int",
                    range=(0, 1439),
                    note="0 unless bedtime was after midnight",
                ),
                F_HOW_SLEPT,
                F_MOOD_SLEEP,
            ),
            note="Marker: night begins. At most one per day.",
        ),
        Category(
            "WOKE_UP",
            "Woke up",
            "date",
            span=False,
            fields=(F_HOW_WOKE, F_MOOD_WAKE),
            note="Marker: night ends. At most one per day.",
        ),
        Category(
            "NIGHT_WAKING",
            "Night waking",
            "uuid",
            span=True,
            fields=(F_MOOD_WAKE,),
            note="Awake phase during the night, with start and end.",
        ),
        # ---- feeding ----
        Category(
            "NURSING",
            "Nursing",
            "uuid",
            span=True,
            fields=(
                Field(
                    "breastLeftMinutes", "int", range=(0, 100_000), note="SECONDS despite the name"
                ),
                Field(
                    "breastRightMinutes", "int", range=(0, 100_000), note="SECONDS despite the name"
                ),
                Field("activeBreast", "enum", ("LEFT", "RIGHT"), note="side last active"),
            ),
            note=(
                "The *Minutes fields count seconds: a six-minute session shows "
                "up as 346 in real data."
            ),
        ),
        Category(
            "BOTTLE",
            "Bottle",
            "uuid",
            span=False,
            fields=(
                Field("bottleFeedingType", "enum", ("FORMULA", "BREAST_MILK")),
                Field("feedingAmount", "int", range=(0, 1000), note="ml"),
            ),
        ),
        Category(
            "SOLIDS",
            "Solids",
            "uuid",
            span=False,
            fields=(Field("feedingAmount", "int", range=(0, 1000), note="ml or g"),),
            note="What was eaten goes into the comment.",
        ),
        Category(
            "PUMPING",
            "Pumping",
            "uuid",
            span=False,
            fields=(
                Field("amountPumpedLeft", "int", range=(0, 1000), note="ml"),
                Field("amountPumpedRight", "int", range=(0, 1000), note="ml"),
            ),
        ),
        # ---- care and health ----
        Category(
            "CHANGED_DIAPER",
            "Diaper change",
            "uuid",
            span=False,
            fields=(Field("diaperContent", "enum", ("DRY", "WET", "DIRTY", "MIXED")),),
        ),
        Category(
            "TEMPERATURE",
            "Temperature",
            "uuid",
            span=False,
            fields=(Field("temperature", "float", range=(30.0, 45.0), note="degrees Celsius"),),
        ),
        Category(
            "MEDICINE",
            "Medicine",
            "uuid",
            span=False,
            note="No dedicated field for the drug - put its name in the comment.",
        ),
    ]
}

# Short names accepted on the command line and in data files (English and
# German, because the tool grew up in a German nursery).
ALIASES = {
    "nap": "NAP",
    "sleep": "NAP",
    "nickerchen": "NAP",
    "schlaf": "NAP",
    "bedtime": "BED_TIME",
    "bed": "BED_TIME",
    "schlafenszeit": "BED_TIME",
    "bett": "BED_TIME",
    "wokeup": "WOKE_UP",
    "woke": "WOKE_UP",
    "wake": "WOKE_UP",
    "aufgewacht": "WOKE_UP",
    "nightwaking": "NIGHT_WAKING",
    "night": "NIGHT_WAKING",
    "nachtwach": "NIGHT_WAKING",
    "nursing": "NURSING",
    "breastfeeding": "NURSING",
    "stillen": "NURSING",
    "bottle": "BOTTLE",
    "flasche": "BOTTLE",
    "flaeschchen": "BOTTLE",
    "fläschchen": "BOTTLE",
    "solids": "SOLIDS",
    "food": "SOLIDS",
    "meal": "SOLIDS",
    "brei": "SOLIDS",
    "essen": "SOLIDS",
    "pumping": "PUMPING",
    "pump": "PUMPING",
    "pumpen": "PUMPING",
    "abpumpen": "PUMPING",
    "diaper": "CHANGED_DIAPER",
    "nappy": "CHANGED_DIAPER",
    "windel": "CHANGED_DIAPER",
    "temperature": "TEMPERATURE",
    "temp": "TEMPERATURE",
    "fever": "TEMPERATURE",
    "temperatur": "TEMPERATURE",
    "fieber": "TEMPERATURE",
    "medicine": "MEDICINE",
    "medication": "MEDICINE",
    "medizin": "MEDICINE",
    "medikament": "MEDICINE",
}

SLEEP_CATEGORIES = ("NAP", "BED_TIME", "WOKE_UP", "NIGHT_WAKING")


def get(name: str) -> Category:
    """Look up a category by API name or alias (case-insensitive)."""
    key = name.strip()
    key = ALIASES.get(key.lower(), key.upper().replace("-", "_").replace(" ", "_"))
    if key not in CATEGORIES:
        known = ", ".join(sorted(CATEGORIES))
        raise CategoryError(f"Unknown category '{name}'. Known: {known}")
    return CATEGORIES[key]


def validate(category: str, values: dict | None) -> dict:
    """Check extra fields against the category; return them properly typed."""
    cat = get(category)
    out: dict = {}
    for name, value in (values or {}).items():
        fld = cat.field(name)
        if fld is None:
            allowed = ", ".join(f.name for f in cat.fields) or "none"
            raise CategoryError(f"{cat.name} has no field '{name}'. Allowed: {allowed}")
        out[name] = _coerce(cat, fld, value)
    return out


def _coerce(cat: Category, fld: Field, value):
    if fld.kind == "enum":
        v = str(value).strip().upper()
        if v not in fld.values:
            raise CategoryError(
                f"{cat.name}.{fld.name}: '{value}' not recognised. Options: {', '.join(fld.values)}"
            )
        return v
    if fld.kind == "enum_list":
        if isinstance(value, str):
            value = [t for t in value.replace(";", ",").split(",") if t.strip()]
        out = []
        for item in value:
            v = str(item).strip().upper()
            if v not in fld.values:
                raise CategoryError(
                    f"{cat.name}.{fld.name}: '{item}' not recognised. "
                    f"Options: {', '.join(fld.values)}"
                )
            out.append(v)
        return out
    if fld.kind in ("int", "float"):
        try:
            number = int(value) if fld.kind == "int" else float(value)
        except (TypeError, ValueError) as err:
            raise CategoryError(f"{cat.name}.{fld.name}: '{value}' is not a number") from err
        if fld.range and not (fld.range[0] <= number <= fld.range[1]):
            raise CategoryError(
                f"{cat.name}.{fld.name}: {number} outside {fld.range[0]}-{fld.range[1]}"
            )
        return number
    raise CategoryError(f"Unknown field kind {fld.kind}")


def overview() -> str:
    """Human-readable listing - what `napper-sync categories` prints."""
    lines = []
    for cat in CATEGORIES.values():
        kind = "span" if cat.span else "point"
        per_day = "one per day" if cat.id_scheme == "date" else "any number"
        lines.append(f"{cat.name:<15} {cat.label:<14} {kind:<6} {per_day}")
        for f in cat.fields:
            if f.kind in ("enum", "enum_list"):
                detail = " | ".join(f.values)
            elif f.range:
                detail = f"{f.range[0]} to {f.range[1]}"
            else:
                detail = f.kind
            note = f"   ({f.note})" if f.note else ""
            lines.append(f"    --set {f.name}=<{detail}>{note}")
        if cat.note:
            lines.append(f"    {cat.note}")
        lines.append("")
    return "\n".join(lines)
