"""
Unit tests for DatabaseManager.purge_batches_older_than() and the
retention config persistence in backend/api/config.py.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.core.schema import BatchRecord, CanonicalLogEvent, LogLevel, TimestampConfidence
from backend.core.store import DatabaseManager


@pytest.fixture
def temp_db(tmp_path):
    return DatabaseManager(db_path=str(tmp_path / "test_logs.db"))


def _make_batch(batch_id: str, days_old: int) -> BatchRecord:
    uploaded_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return BatchRecord(batch_id=batch_id, file_name=f"{batch_id}.log", file_size_bytes=10, total_events=1, uploaded_at=uploaded_at)


def _make_event(batch_id: str, event_id: str) -> CanonicalLogEvent:
    return CanonicalLogEvent(
        event_id=event_id, batch_id=batch_id, file_name="x.log", line_no=1,
        ts_utc="2026-08-05T10:00:00Z", ts_raw="x", ts_confidence=TimestampConfidence.PARSED,
        level=LogLevel.INFO, source_system="app", component="c", message="m", raw="r",
    )


def test_purge_removes_only_batches_older_than_cutoff(temp_db):
    temp_db.insert_batch_and_events(_make_batch("old", days_old=100), [_make_event("old", "e1")])
    temp_db.insert_batch_and_events(_make_batch("recent", days_old=1), [_make_event("recent", "e2")])

    result = temp_db.purge_batches_older_than(30)

    assert result == {"batches_purged": 1, "events_purged": 1}
    remaining = {b["batch_id"] for b in temp_db.list_batches()}
    assert remaining == {"recent"}


def test_purge_removes_matching_events(temp_db):
    temp_db.insert_batch_and_events(_make_batch("old", days_old=100), [_make_event("old", "e1")])

    temp_db.purge_batches_older_than(30)

    _, total = temp_db.query_events()
    assert total == 0


def test_purge_cleans_up_fts_index(temp_db):
    batch = _make_batch("old", days_old=100)
    event = CanonicalLogEvent(
        event_id="e1", batch_id="old", file_name="x.log", line_no=1,
        ts_utc="2026-08-05T10:00:00Z", ts_raw="x", ts_confidence=TimestampConfidence.PARSED,
        level=LogLevel.INFO, source_system="app", component="c",
        message="a searchable needle", raw="r",
    )
    temp_db.insert_batch_and_events(batch, [event])

    temp_db.purge_batches_older_than(30)

    _, total = temp_db.query_events(search_term="needle")
    assert total == 0


def test_purge_with_no_stale_batches_is_a_noop(temp_db):
    temp_db.insert_batch_and_events(_make_batch("recent", days_old=1), [_make_event("recent", "e1")])
    result = temp_db.purge_batches_older_than(30)
    assert result == {"batches_purged": 0, "events_purged": 0}
    assert len(temp_db.list_batches()) == 1


# ---------------------------------------------------------------------------
# Settings persistence (backend/api/config.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_instance(tmp_path, monkeypatch):
    """A real Settings() pointed at a scratch config.yaml, following the
    same construction the module-level `settings` singleton uses, without
    touching the real backend/config.yaml."""
    import yaml
    from backend.api import config as config_module

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump({"db_path": "data/logs.db", "profiles_dir": str(tmp_path / "profiles")}))
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_module, "BACKEND_DIR", tmp_path)
    return config_module.Settings()


def test_retention_disabled_by_default(settings_instance):
    """A fresh config.yaml with no retention_days key must come up
    disabled -- an existing install upgrading into this feature must
    never start silently deleting data just because the field now
    exists."""
    assert settings_instance.retention_days is None


def test_update_retention_config_persists(settings_instance):
    settings_instance.update_retention_config(90)
    assert settings_instance.retention_days == 90
    assert settings_instance.get_retention_config() == {"retention_days": 90}


def test_update_retention_config_zero_normalizes_to_disabled(settings_instance):
    settings_instance.update_retention_config(90)
    settings_instance.update_retention_config(0)
    assert settings_instance.retention_days is None
