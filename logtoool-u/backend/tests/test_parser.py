"""
Unit tests for deterministic parser core components:
- Timestamp parsing & timezone conversion
- Level normalization
- Multiline grouping
- Profile scoring and match ratio evaluation
"""

import pytest
from datetime import datetime, timezone
from backend.core.schema import LogLevel, TimestampConfidence, ParserProfile, ProfileType
from backend.core.timezones import parse_and_convert_timestamp
from backend.core.schema import normalize_level
from backend.core.parse import group_multiline_logs, evaluate_profile_match


def test_timestamp_parsing_utc():
    ts_str = "2026-08-05T14:30:00Z"
    iso_utc, conf = parse_and_convert_timestamp(ts_str)
    assert iso_utc == "2026-08-05T14:30:00Z"
    assert conf == TimestampConfidence.PARSED


def test_timestamp_parsing_unparseable():
    upload_time = datetime(2026, 8, 5, 20, 0, 0, tzinfo=timezone.utc)
    iso_utc, conf = parse_and_convert_timestamp("invalid_junk_string", upload_time=upload_time)
    assert iso_utc == "2026-08-05T20:00:00Z"
    assert conf == TimestampConfidence.UNPARSEABLE


def test_level_normalization():
    assert normalize_level("DEBUG") == LogLevel.DEBUG
    assert normalize_level("WARNING") == LogLevel.WARN
    assert normalize_level("ERR") == LogLevel.ERROR
    assert normalize_level("FATAL") == LogLevel.CRITICAL
    assert normalize_level("200", custom_map={"200": "INFO"}) == LogLevel.INFO
    assert normalize_level("UNKNOWN_SPECIAL_FLAG") == LogLevel.UNKNOWN


def test_multiline_grouping():
    raw_lines = [
        (1, "2026-08-05 15:00:00 [ERROR] App error"),
        (2, "java.lang.NullPointerException: null pointer"),
        (3, "\tat com.example.Main.main(Main.java:10)"),
        (4, "2026-08-05 15:01:00 [INFO] System recovered")
    ]
    grouped = group_multiline_logs(raw_lines)
    assert len(grouped) == 2
    assert grouped[0].start_line_no == 1
    assert len(grouped[0].continuation_lines) == 2
    assert "NullPointerException" in grouped[0].full_raw
    assert grouped[1].start_line_no == 4


def test_profile_scoring():
    profile = ParserProfile(
        name="Test Regex",
        type=ProfileType.REGEX,
        pattern=r'^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>\w+)\] (?P<message>.*)$',
        timestamp_field="timestamp",
        level_field="level",
        message_field="message",
        min_match_ratio=0.8
    )

    raw_lines = [
        (1, "2026-08-05 15:00:00 [ERROR] App error"),
        (2, "2026-08-05 15:01:00 [INFO] System recovered")
    ]
    grouped = group_multiline_logs(raw_lines)
    score, events = evaluate_profile_match(grouped, profile)
    assert score == 1.0
    assert len(events) == 2
