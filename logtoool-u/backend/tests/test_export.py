"""
Unit tests for GET /api/logs/events/export -- calls the route function
directly (same pattern the rest of this package uses for testing business
logic without going through FastAPI's TestClient/dependency-injection
machinery, see test_alert_rules.py) rather than through HTTP, since the
route body is what actually needs covering here.
"""
import csv
import io
import json

import pytest

from backend.api.routes.logs import export_events
from backend.core.schema import BatchRecord, CanonicalLogEvent, LogLevel, TimestampConfidence
from backend.core.store import DatabaseManager


@pytest.fixture
def seeded_db(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "test.db"))
    batch = BatchRecord(batch_id="batch_001", file_name="test.log", file_size_bytes=100, total_events=2)
    events = [
        CanonicalLogEvent(
            event_id="e1", batch_id="batch_001", file_name="test.log", line_no=1,
            ts_utc="2026-08-05T10:00:00Z", ts_raw="2026-08-05 10:00:00",
            ts_confidence=TimestampConfidence.PARSED, level=LogLevel.ERROR,
            source_system="payments", component="auth", message="token expired",
            raw="2026-08-05 10:00:00 [ERROR] token expired",
        ),
        CanonicalLogEvent(
            event_id="e2", batch_id="batch_001", file_name="test.log", line_no=2,
            ts_utc="2026-08-05T10:01:00Z", ts_raw="2026-08-05 10:01:00",
            ts_confidence=TimestampConfidence.PARSED, level=LogLevel.INFO,
            source_system="payments", component="db", message="query ok",
            raw="2026-08-05 10:01:00 [INFO] query ok",
        ),
    ]
    db.insert_batch_and_events(batch, events)
    return db


def _call(db, **kwargs):
    defaults = dict(
        format="csv", level=None, source_system=None, component=None,
        search_term=None, batch_id=None, date_from=None, date_to=None,
    )
    defaults.update(kwargs)
    return export_events(_user=None, db=db, **defaults)


def test_csv_export_contains_every_matching_row(seeded_db):
    response = _call(seeded_db, format="csv")
    assert response.media_type == "text/csv"
    rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))
    assert len(rows) == 2
    assert {r["event_id"] for r in rows} == {"e1", "e2"}


def test_csv_export_respects_filters(seeded_db):
    response = _call(seeded_db, format="csv", level="ERROR")
    rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["event_id"] == "e1"


def test_csv_export_flattens_attributes_to_json_string(seeded_db):
    response = _call(seeded_db, format="csv")
    rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))
    # attributes is a dict on the underlying event -- must round-trip as a JSON string, not "[object]" or blow up the CSV.
    for r in rows:
        json.loads(r["attributes"])


def test_json_export_shape(seeded_db):
    response = _call(seeded_db, format="json")
    assert response.media_type == "application/json"
    payload = json.loads(response.body)
    assert payload["total"] == 2
    assert payload["exported"] == 2
    assert len(payload["events"]) == 2


def test_export_content_disposition_header_present(seeded_db):
    csv_response = _call(seeded_db, format="csv")
    assert "attachment" in csv_response.headers["content-disposition"]
    json_response = _call(seeded_db, format="json")
    assert "attachment" in json_response.headers["content-disposition"]
