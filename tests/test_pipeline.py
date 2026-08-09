import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, matcher, parser, profiles, reader

SAMPLE = """2026-08-05 10:00:01,123 - app.main - INFO - Application started
2026-08-05 10:00:02,456 - app.auth - INFO - User logged in
2026-08-05 10:00:03,789 - app.db - ERROR - Connection refused
2026-08-05 10:00:04,012 - app.main - CRITICAL - Disk full
"""


@pytest.fixture
def py_profile():
    return profiles.load_profile("python_logging")


def test_read_entries_counts_lines():
    entries = reader.read_entries(SAMPLE)
    assert len(entries) == 4
    assert entries[0]["line_no"] == 1
    assert entries[3]["line_no"] == 4


def test_read_entries_multiline_grouping():
    text = "2026-01-01 00:00:00,000 - app.main - INFO - start\n  continuation\nnext"
    entries = reader.read_entries(text, continuation_regex=r"^\s")
    assert len(entries) == 2
    assert "continuation" in entries[0]["raw"]
    assert entries[1]["line_no"] == 3


def test_matcher_picks_correct_profile():
    entries = reader.read_entries(SAMPLE)
    all_profiles = list(profiles.load_all().values())
    best, ratio = matcher.select(entries, all_profiles)
    assert best["name"] == "python_logging"
    assert ratio == 1.0


def test_parser_normalizes_levels(py_profile):
    entries = reader.read_entries(SAMPLE)
    events = parser.parse_entries(entries, py_profile, batch_id=7)
    assert len(events) == 4
    assert [e.level for e in events] == ["INFO", "INFO", "ERROR", "CRITICAL"]
    assert all(e.batch_id == 7 for e in events)


def test_parser_parses_timestamp(py_profile):
    entries = reader.read_entries(SAMPLE)
    events = parser.parse_entries(entries, py_profile, batch_id=7)
    assert events[0].ts_utc == datetime(2026, 8, 5, 10, 0, 1, 123000)
    assert events[0].ts_raw == "2026-08-05 10:00:01,123"


def test_parser_unknown_level_maps_to_unknown(py_profile):
    text = "2026-08-05 10:00:01,123 - app.main - TRACE - some noise"
    events = parser.parse_entries(reader.read_entries(text), py_profile, batch_id=1)
    assert events[0].level == "UNKNOWN"


def test_db_roundtrip():
    con = db.get_connection(":memory:")
    profile = profiles.load_profile("python_logging")
    entries = reader.read_entries(SAMPLE)
    batch_id = db.insert_batch(
        con, file_name="test.log", profile_name="python_logging", row_count=len(entries)
    )
    events = parser.parse_entries(entries, profile, batch_id)
    db.insert_events(con, events)

    df = db.events_to_df(con, batch_id)
    assert len(df) == 4
    assert set(df["level"]) == {"INFO", "ERROR", "CRITICAL"}


def test_db_batch_id_increments():
    con = db.get_connection(":memory:")
    first = db.insert_batch(con, file_name="a.log")
    second = db.insert_batch(con, file_name="b.log")
    assert second == first + 1
