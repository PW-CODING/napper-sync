from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from napper_sync import sources
from napper_sync.categories import CategoryError
from napper_sync.sync import Entry, is_duplicate, parse_time, sync

T = datetime.fromisoformat("2026-09-18T10:00:00").astimezone()


def nap(start, minutes=60, **kw):
    return Entry(start=start, end=start + timedelta(minutes=minutes), category="NAP", **kw)


class TestEntry:
    def test_point_category_forces_end_equal_start(self):
        e = Entry(start=T, end=T + timedelta(hours=1), category="diaper")
        assert e.end == e.start

    def test_span_requires_end_after_start(self):
        with pytest.raises(ValueError, match="not after start"):
            Entry(start=T, end=T, category="nap")

    def test_fields_are_validated(self):
        with pytest.raises(CategoryError):
            Entry(start=T, category="diaper", fields={"diaperContent": "PURPLE"})

    def test_label(self):
        e = nap(T, fields={"howBabySlept": "SWING"}, comment="(Famly)")
        assert e.label() == 'NAP 2026-09-18 10:00 -> 11:00  howBabySlept=SWING  "(Famly)"'


class TestDuplicates:
    def test_same_start_within_fuzz(self):
        assert is_duplicate(nap(T + timedelta(minutes=8)), [nap(T)])
        # 15 minutes apart but non-overlapping: distinct naps
        assert not is_duplicate(nap(T + timedelta(minutes=15), 10), [nap(T, 10)])

    def test_dry_run_also_collapses_source_duplicates(self, client, mock_api):
        result = sync(
            client,
            "baby-1",
            [nap(T), nap(T + timedelta(minutes=3))],
            live=False,
            report=lambda *_: None,
        )
        assert result.planned == 1 and result.skipped == 1

    def test_overlapping_spans(self):
        assert is_duplicate(nap(T + timedelta(minutes=30), 60), [nap(T, 60)])

    def test_different_category_never_matches(self):
        bottle = Entry(start=T, category="bottle")
        assert not is_duplicate(bottle, [Entry(start=T, category="diaper")])

    def test_day_marker_matches_same_day_regardless_of_time(self):
        a = Entry(start=T, category="bedtime")
        b = Entry(start=T + timedelta(hours=5), category="bedtime")
        assert is_duplicate(a, [b])
        c = Entry(start=T + timedelta(days=1), category="bedtime")
        assert not is_duplicate(a, [c])


class TestParseTime:
    def test_naive_is_local(self):
        dt = parse_time("2026-09-18T10:00:00")
        assert dt == datetime(2026, 9, 18, 10, 0).astimezone()
        assert dt.hour == 10 and dt.tzinfo is not None

    def test_offset_is_converted_to_local(self):
        instant = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
        dt = parse_time("2026-09-18T08:00:00+00:00")
        assert dt == instant
        assert dt.utcoffset() == instant.astimezone().utcoffset()

    def test_z_suffix(self):
        assert parse_time("2026-09-18T08:00:00Z") == datetime(2026, 9, 18, 8, 0, tzinfo=UTC)


class TestSync:
    def test_dry_run_writes_nothing(self, client, mock_api):
        result = sync(client, "baby-1", [nap(T)], live=False, report=lambda *_: None)
        assert result.planned == 1 and result.written == 0
        assert not mock_api.logs

    def test_live_writes_only_new_entries(self, client, mock_api):
        entries = [
            nap(T),
            Entry(start=T + timedelta(hours=2), category="diaper", fields={"diaperContent": "WET"}),
        ]
        first = sync(client, "baby-1", entries, live=True, report=lambda *_: None)
        assert first.written == 2

        second = sync(client, "baby-1", entries, live=True, report=lambda *_: None)
        assert second.written == 0 and second.skipped == 2
        assert len(mock_api.logs) == 2

    def test_near_duplicate_against_existing_data_is_skipped(self, client, mock_api):
        sync(client, "baby-1", [nap(T)], live=True, report=lambda *_: None)
        shifted = nap(T + timedelta(minutes=5))
        result = sync(client, "baby-1", [shifted], live=True, report=lambda *_: None)
        assert result.skipped == 1 and len(mock_api.logs) == 1

    def test_duplicates_within_one_run(self, client, mock_api):
        result = sync(
            client,
            "baby-1",
            [nap(T), nap(T + timedelta(minutes=3))],
            live=True,
            report=lambda *_: None,
        )
        assert result.written == 1

    def test_empty_source(self, client, mock_api):
        result = sync(client, "baby-1", [], live=True, report=lambda *_: None)
        assert result.written == 0 and not mock_api.calls


class TestSources:
    def test_csv(self, tmp_path):
        path = tmp_path / "in.csv"
        path.write_text(
            "start,end,category,comment,feedingAmount,bottleFeedingType,diaperContent\n"
            "2026-09-17T13:10:00,2026-09-17T14:35:00,nap,,,,\n"
            "2026-09-17T15:00:00,,bottle,,120,FORMULA,\n"
            "2026-09-17T15:30:00,,windel,,,,WET\n"
            ",,,,,,\n",  # blank line is ignored
            encoding="utf-8",
        )
        entries = sources.load(path)
        assert [e.category for e in entries] == ["NAP", "BOTTLE", "CHANGED_DIAPER"]
        assert entries[1].fields == {"feedingAmount": 120, "bottleFeedingType": "FORMULA"}
        assert entries[2].fields == {"diaperContent": "WET"}

    def test_json_with_grouped_and_flat_fields(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text(
            json.dumps(
                [
                    {"start": "2026-09-17T15:00:00", "category": "bottle", "feedingAmount": 120},
                    {
                        "start": "2026-09-17T13:00:00",
                        "end": "2026-09-17T14:00:00",
                        "category": "nap",
                        "fields": {"howBabySlept": "SWING"},
                    },
                ]
            ),
            encoding="utf-8",
        )
        entries = sources.load(path)
        assert entries[0].fields == {"feedingAmount": 120}
        assert entries[1].fields == {"howBabySlept": "SWING"}

    def test_unknown_format(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown source format"):
            sources.load(tmp_path / "x.xml")


# --- corrections: a source entry with a stable ID updates instead of duplicating

NAP_ID = "LOG_bb35e41e-c4e8-512f-b1e3-e5b11c73e982_NAP"


def quiet(*_):
    pass


def puts(mock_api):
    return [c for c in mock_api.calls if c[0] == "PUT"]


class TestCorrections:
    def test_changed_end_corrects_the_existing_entry(self, client, mock_api):
        first = sync(client, "baby-1", [nap(T, 60, log_id=NAP_ID)], live=True, report=quiet)
        assert first.written == 1

        second = sync(client, "baby-1", [nap(T, 75, log_id=NAP_ID)], live=True, report=quiet)

        assert (second.written, second.updated, second.skipped) == (0, 1, 0)
        assert len(mock_api.logs) == 1
        want_end = (T + timedelta(minutes=75)).isoformat(timespec="milliseconds")
        assert mock_api.logs[NAP_ID]["end"] == want_end

    def test_unchanged_entry_is_not_rewritten(self, client, mock_api):
        sync(client, "baby-1", [nap(T, 60, log_id=NAP_ID)], live=True, report=quiet)
        before = len(puts(mock_api))

        second = sync(client, "baby-1", [nap(T, 60, log_id=NAP_ID)], live=True, report=quiet)

        assert (second.written, second.updated, second.skipped) == (0, 0, 1)
        assert len(puts(mock_api)) == before

    def test_changed_comment_alone_is_a_correction(self, client, mock_api):
        sync(
            client,
            "baby-1",
            [nap(T, 60, log_id=NAP_ID, comment="(Famly)")],
            live=True,
            report=quiet,
        )

        second = sync(
            client,
            "baby-1",
            [nap(T, 60, log_id=NAP_ID, comment="woke up happy (Famly)")],
            live=True,
            report=quiet,
        )

        assert second.updated == 1
        assert mock_api.logs[NAP_ID]["comment"] == "woke%20up%20happy%20%28Famly%29"

    def test_entry_without_id_matched_by_time_is_left_alone(self, client, mock_api):
        # Written by an older version (random ID) or by hand in the app.
        sync(client, "baby-1", [nap(T, 60)], live=True, report=quiet)

        second = sync(client, "baby-1", [nap(T, 75, log_id=NAP_ID)], live=True, report=quiet)

        assert (second.written, second.updated, second.skipped) == (0, 0, 1)
        assert len(mock_api.logs) == 1 and NAP_ID not in mock_api.logs

    def test_dry_run_lists_the_correction_but_writes_nothing(self, client, mock_api):
        sync(client, "baby-1", [nap(T, 60, log_id=NAP_ID)], live=True, report=quiet)
        before = len(puts(mock_api))
        lines: list[str] = []

        result = sync(
            client, "baby-1", [nap(T, 75, log_id=NAP_ID)], live=False, report=lines.append
        )

        assert result.planned == 1 and result.updated == 0 and result.written == 0
        assert len(puts(mock_api)) == before
        assert any("11:00 -> 11:15" in line for line in lines), lines

    def test_correction_keeps_fields_added_in_the_app(self, client, mock_api):
        sync(client, "baby-1", [nap(T, 60, log_id=NAP_ID)], live=True, report=quiet)
        mock_api.logs[NAP_ID]["howBabySlept"] = "SWING"  # the parent added this in the app

        sync(client, "baby-1", [nap(T, 75, log_id=NAP_ID)], live=True, report=quiet)

        assert mock_api.logs[NAP_ID]["howBabySlept"] == "SWING"

    def test_changed_field_from_the_source_is_a_correction(self, client, mock_api):
        wet = Entry(
            start=T,
            category="diaper",
            fields={"diaperContent": "WET"},
            log_id=NAP_ID[:-4] + "_CHANGED_DIAPER",
        )
        sync(client, "baby-1", [wet], live=True, report=quiet)
        mixed = Entry(
            start=T, category="diaper", fields={"diaperContent": "MIXED"}, log_id=wet.log_id
        )

        result = sync(client, "baby-1", [mixed], live=True, report=quiet)

        assert result.updated == 1
        assert mock_api.logs[wet.log_id]["diaperContent"] == "MIXED"


class TestSourceIds:
    def test_csv_id_column_becomes_log_id(self, tmp_path):
        path = tmp_path / "in.csv"
        path.write_text(
            "id,start,end,category,comment\n"
            f"{NAP_ID},2026-09-17T13:10:00,2026-09-17T14:35:00,nap,\n"
            ",2026-09-17T15:30:00,,diaper,\n",
            encoding="utf-8",
        )
        entries = sources.load(path)
        assert entries[0].log_id == NAP_ID
        assert entries[0].fields == {}
        assert entries[1].log_id is None

    def test_json_id_key_becomes_log_id(self, tmp_path):
        path = tmp_path / "in.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "id": NAP_ID,
                        "start": "2026-09-17T13:00:00",
                        "end": "2026-09-17T14:00:00",
                        "category": "nap",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (entry,) = sources.load(path)
        assert entry.log_id == NAP_ID and entry.fields == {}


class TestExistingEntriesAreReadLeniently:
    def test_unknown_field_value_in_napper_still_blocks_a_duplicate(self, client, mock_api):
        client.create_log("baby-1", "diaper", T, fields={"diaperContent": "WET"}, dry_run=False)
        (log_id,) = mock_api.logs
        mock_api.logs[log_id]["diaperContent"] = "PURPLE"  # a value this version does not know
        again = Entry(start=T, category="diaper", fields={"diaperContent": "WET"})

        result = sync(client, "baby-1", [again], live=True, report=quiet)

        assert result.written == 0 and len(mock_api.logs) == 1
