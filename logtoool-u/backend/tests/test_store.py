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


# ---------------------------------------------------------------------------
# Full-text / field-scoped search (events_fts, search_query.py)
# ---------------------------------------------------------------------------


def _seed_search_events(db):
    batch = BatchRecord(batch_id="search_batch", file_name="search.log", file_size_bytes=100, total_events=3)
    events = [
        CanonicalLogEvent(
            event_id="s1", batch_id="search_batch", file_name="search.log", line_no=1,
            ts_utc="2026-08-05T10:00:00Z", ts_raw="x", ts_confidence=TimestampConfidence.PARSED,
            level=LogLevel.ERROR, source_system="cardinal", component="auth",
            message="connection refused to order-ABC123", raw="raw1",
        ),
        CanonicalLogEvent(
            event_id="s2", batch_id="search_batch", file_name="search.log", line_no=2,
            ts_utc="2026-08-05T10:01:00Z", ts_raw="x", ts_confidence=TimestampConfidence.PARSED,
            level=LogLevel.INFO, source_system="cardinal", component="db",
            message="query ok", raw="raw2",
        ),
        CanonicalLogEvent(
            event_id="s3", batch_id="search_batch", file_name="search.log", line_no=3,
            ts_utc="2026-08-05T10:02:00Z", ts_raw="x", ts_confidence=TimestampConfidence.PARSED,
            level=LogLevel.ERROR, source_system="netcetera", component="auth",
            message="timeout waiting for response", raw="raw3",
        ),
    ]
    db.insert_batch_and_events(batch, events)


def test_search_matches_mid_token_substring(temp_db):
    """Trigram tokenizer must preserve the old LIKE '%term%' behavior of
    matching inside a token, not just at word boundaries."""
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(search_term="der-AB")
    assert total == 1
    assert results[0]["event_id"] == "s1"


def test_search_is_case_insensitive(temp_db):
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(search_term="CONNECTION")
    assert total == 1
    assert results[0]["event_id"] == "s1"


def test_search_field_value_level(temp_db):
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(search_term="level:error")
    assert total == 2
    assert {r["event_id"] for r in results} == {"s1", "s3"}


def test_search_field_value_source(temp_db):
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(search_term="source:cardinal")
    assert total == 2
    assert {r["event_id"] for r in results} == {"s1", "s2"}


def test_search_quoted_phrase(temp_db):
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(search_term='"connection refused"')
    assert total == 1
    assert results[0]["event_id"] == "s1"


def test_search_field_and_free_text_combine(temp_db):
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(search_term="level:ERROR timeout")
    assert total == 1
    assert results[0]["event_id"] == "s3"


def test_search_contradictory_dropdown_and_field_yields_empty(temp_db):
    """The dropdown-selected level and a typed level: token apply as two
    separate equality filters on the same column -- a disagreement
    naturally ANDs to zero rows rather than one silently overriding the
    other."""
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(level="INFO", search_term="level:ERROR")
    assert total == 0


def test_search_no_match_returns_empty(temp_db):
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(search_term="nonexistent-zzz")
    assert total == 0


def test_search_unrecognized_field_token_treated_as_free_text(temp_db):
    """merchant:acme isn't a recognized field -- it should be searched for
    literally as free text, not silently dropped or applied as a filter."""
    _seed_search_events(temp_db)
    results, total = temp_db.query_events(search_term="merchant:acme")
    assert total == 0  # no event's message/raw/component contains that literal string


def test_fts_index_cleaned_up_on_batch_delete(temp_db):
    _seed_search_events(temp_db)
    assert temp_db.delete_batch("search_batch") is True
    results, total = temp_db.query_events(search_term="timeout")
    assert total == 0


def test_fts_index_cleaned_up_on_clear_all(temp_db):
    _seed_search_events(temp_db)
    temp_db.clear_all()
    results, total = temp_db.query_events(search_term="timeout")
    assert total == 0


def test_migration_creates_fts_index_and_backfills_pre_existing_events(tmp_path):
    """A database created before events_fts existed must still work --
    _ensure_fts_index() has to CREATE VIRTUAL TABLE and backfill from the
    already-populated events table, not assume Base.metadata.create_all()
    (which never touches an existing DB file) is enough."""
    from sqlalchemy import create_engine, text

    db_path = str(tmp_path / "pre_fts.db")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE batches (
                    batch_id VARCHAR PRIMARY KEY, file_name VARCHAR NOT NULL,
                    file_size_bytes INTEGER NOT NULL, total_events INTEGER,
                    matched_profile VARCHAR, matched_profile_version VARCHAR,
                    match_ratio FLOAT, uploaded_at VARCHAR NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE events (
                    event_id VARCHAR PRIMARY KEY, batch_id VARCHAR NOT NULL,
                    file_name VARCHAR NOT NULL, line_no INTEGER NOT NULL,
                    ts_utc VARCHAR, ts_raw VARCHAR NOT NULL, ts_confidence VARCHAR NOT NULL,
                    level VARCHAR NOT NULL, source_system VARCHAR NOT NULL,
                    component VARCHAR, message TEXT NOT NULL, raw TEXT NOT NULL, attributes TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO batches VALUES ('b1', 'f.log', 10, 1, NULL, NULL, 1.0, '2026-08-05T00:00:00Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO events VALUES ('e1', 'b1', 'f.log', 1, '2026-08-05T00:00:00Z', 'x', 'PARSED', "
                "'ERROR', 'app', 'auth', 'pre-existing searchable message', 'raw', NULL)"
            )
        )
        conn.commit()
    engine.dispose()

    db = DatabaseManager(db_path=db_path)  # must not raise
    results, total = db.query_events(search_term="searchable")
    assert total == 1
    assert results[0]["event_id"] == "e1"
