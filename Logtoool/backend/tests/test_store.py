"""
Unit tests for database manager and SQLite storage engine.
Tests batch insertions, server-side pagination, and SQL filtering.
"""

import os
import pytest
from backend.core.schema import BatchRecord, CanonicalLogEvent, LogLevel, TimestampConfidence
from backend.core.store import DatabaseManager


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_logs.db"
    db_mgr = DatabaseManager(db_path=str(db_file))
    return db_mgr


def test_batch_insertion_and_pagination(temp_db):
    batch = BatchRecord(
        batch_id="batch_001",
        file_name="test.log",
        file_size_bytes=1000,
        total_events=3,
        matched_profile="TestProfile",
        match_ratio=1.0
    )

    events = [
        CanonicalLogEvent(
            event_id="e1",
            batch_id="batch_001",
            file_name="test.log",
            line_no=1,
            ts_utc="2026-08-05T10:00:00Z",
            ts_raw="2026-08-05 10:00:00",
            ts_confidence=TimestampConfidence.PARSED,
            level=LogLevel.INFO,
            source_system="app",
            component="auth",
            message="User logged in",
            raw="2026-08-05 10:00:00 [INFO] User logged in"
        ),
        CanonicalLogEvent(
            event_id="e2",
            batch_id="batch_001",
            file_name="test.log",
            line_no=2,
            ts_utc="2026-08-05T10:01:00Z",
            ts_raw="2026-08-05 10:01:00",
            ts_confidence=TimestampConfidence.PARSED,
            level=LogLevel.ERROR,
            source_system="app",
            component="auth",
            message="Failed login attempt",
            raw="2026-08-05 10:01:00 [ERROR] Failed login attempt"
        ),
        CanonicalLogEvent(
            event_id="e3",
            batch_id="batch_001",
            file_name="test.log",
            line_no=3,
            ts_utc="2026-08-05T10:02:00Z",
            ts_raw="2026-08-05 10:02:00",
            ts_confidence=TimestampConfidence.PARSED,
            level=LogLevel.CRITICAL,
            source_system="db",
            component="storage",
            message="Disk full",
            raw="2026-08-05 10:02:00 [CRITICAL] Disk full"
        )
    ]

    temp_db.insert_batch_and_events(batch, events)

    # Test server-side query with filter
    results, count = temp_db.query_events(page=1, page_size=200, level="ERROR")
    assert count == 1
    assert results[0]["event_id"] == "e2"
    assert results[0]["level"] == "ERROR"

    # Test pagination count
    all_results, all_count = temp_db.query_events(page=1, page_size=2)
    assert all_count == 3
    assert len(all_results) == 2
