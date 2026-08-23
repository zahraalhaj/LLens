"""
API-level tests for /api/analytics/* -- same fixture pattern as
test_cardinal_api.py. Proves the full stack (auth, routing, real file-
upload ingestion, and the Phase 10 pipeline) works end-to-end, not just
the unit-level pipeline/dashboard functions (see test_pipeline.py).
"""
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

ALL_CACHED_DEPS = [
    "get_db", "get_profile_manager", "get_auth_service", "get_ollama_client", "get_rate_limiter",
    "get_ingestion_engine", "get_explainer", "get_chat_assistant", "get_profile_generator",
    "get_email_dispatcher", "get_dedup_engine", "get_alert_rule_manager", "get_alert_processor",
    "get_remote_machine_service",
]

PROFILE_NAME = "Cardinal OTP/StepUp/OOB Log (Multi-Flow Correlation)"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.api import deps
    from backend.api.config import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "profiles_dir", str(tmp_path / "profiles"))
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "ollama_url", "http://localhost:1")

    Path(settings.profiles_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)

    for name in ALL_CACHED_DEPS:
        getattr(deps, name).cache_clear()

    from backend.api.main import app
    with TestClient(app) as c:
        yield c

    for name in ALL_CACHED_DEPS:
        getattr(deps, name).cache_clear()


@pytest.fixture
def admin_client(client):
    from backend.api.deps import get_auth_service
    get_auth_service().create_user("admin", "adminpass123", role="admin")
    client.post("/api/auth/login", json={"username": "admin", "password": "adminpass123"})
    client.post("/api/auth/change-password", json={"current_password": "adminpass123", "new_password": "adminpass123-real"})
    return client


_T0 = datetime.now(timezone.utc) - timedelta(hours=1)

SAMPLE_LOG = (
    f'{_T0.strftime("%-m/%-d/%Y %I:%M:%S %p")} Log Tracker No: SU1 => Stepup Response to Cardinal: '
    '{"TransactionId": "TXN1", "IssuerId": "ISS1", "Status": "SUCCESS", "MerchantInfo": {"MerchantName": "Store A"}}\n'
)


def _ingest_sample(admin_client):
    files = {"file": ("cardinal.log", io.BytesIO(SAMPLE_LOG.encode()), "application/octet-stream")}
    resp = admin_client.post(f"/api/logs/upload?profile_name={quote(PROFILE_NAME)}", files=files)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth required on every route
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/api/analytics/service-overview",
        "/api/analytics/dependency-health",
        "/api/analytics/queue-messaging",
        "/api/analytics/investigation/search?field=transaction_id&query=X",
        "/api/analytics/investigation/search-raw?query=X",
        "/api/analytics/correlation-explorer",
        "/api/analytics/security-quality",
    ],
)
def test_unauthenticated_request_rejected(client, path):
    assert client.get(path).status_code == 401


# ---------------------------------------------------------------------------
# Correlation explorer + raw-text search (new investigator-facing additions)
# ---------------------------------------------------------------------------


def test_correlation_explorer_empty_before_ingestion(admin_client):
    resp = admin_client.get("/api/analytics/correlation-explorer")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"conflicts": [], "candidate_links": [], "low_confidence_hints": [], "graph": {"nodes": [], "edges": []}}


def test_investigation_search_raw_finds_ingested_sample(admin_client):
    _ingest_sample(admin_client)
    resp = admin_client.get("/api/analytics/investigation/search-raw?query=TXN1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["match_count"] == 1
    assert body["results"][0]["case_summary"]["transaction_id"] == "TXN1"


def test_investigation_search_raw_no_match(admin_client):
    _ingest_sample(admin_client)
    resp = admin_client.get("/api/analytics/investigation/search-raw?query=no-such-fragment-anywhere")
    assert resp.status_code == 200
    assert resp.json()["match_count"] == 0


# ---------------------------------------------------------------------------
# Service overview
# ---------------------------------------------------------------------------

def test_service_overview_empty_and_after_ingestion(admin_client):
    resp = admin_client.get("/api/analytics/service-overview")
    assert resp.status_code == 200
    assert resp.json()["total_flows"] == 0

    _ingest_sample(admin_client)
    resp = admin_client.get("/api/analytics/service-overview?lookback_hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_flows"] == 1
    for key in ("success", "error", "pending", "incomplete"):
        assert key in body  # kept as separate, always-present states


# ---------------------------------------------------------------------------
# Dependency health
# ---------------------------------------------------------------------------

def test_dependency_health_returns_all_six_dependencies(admin_client):
    resp = admin_client.get("/api/analytics/dependency-health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["dependencies"].keys()) == {
        "V_PLUS", "POSTILION", "DATABASE_SQL", "BANK_API", "OOB_API", "OTP_ONLINE_PROCESSOR",
    }
    assert "queues" in body


def test_dependency_p95_trend_unknown_dependency_returns_empty(admin_client):
    resp = admin_client.get("/api/analytics/dependency-health/NOT_A_DEPENDENCY/trend")
    assert resp.status_code == 200
    assert resp.json()["buckets"] == []


# ---------------------------------------------------------------------------
# Queue and messaging
# ---------------------------------------------------------------------------

def test_queue_messaging_shape(admin_client):
    resp = admin_client.get("/api/analytics/queue-messaging")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("generated", "application_queued", "processor_received", "downstream_routed", "unmatched", "orphan", "queue_distribution", "handoff_latency"):
        assert key in body


# ---------------------------------------------------------------------------
# Investigation
# ---------------------------------------------------------------------------

def test_investigation_search_invalid_field_rejected(admin_client):
    resp = admin_client.get("/api/analytics/investigation/search?field=not_a_field&query=x")
    assert resp.status_code == 422


def test_investigation_search_by_transaction_id_after_ingestion(admin_client):
    _ingest_sample(admin_client)
    resp = admin_client.get("/api/analytics/investigation/search?field=transaction_id&query=TXN1&lookback_hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert body["match_count"] == 1
    result = body["results"][0]
    assert result["case_summary"]["transaction_id"] == "TXN1"
    assert result["case_summary"]["merchant_name"] == "Store A"
    assert "timeline" in result and "findings" in result and "correlation" in result


def test_investigation_search_no_match_returns_empty_results(admin_client):
    resp = admin_client.get("/api/analytics/investigation/search?field=transaction_id&query=NOPE&lookback_hours=24")
    assert resp.status_code == 200
    assert resp.json()["match_count"] == 0


def test_investigation_detail_not_found_returns_404(admin_client):
    resp = admin_client.get("/api/analytics/investigation/flow-does-not-exist")
    assert resp.status_code == 404


def test_investigation_detail_by_flow_id_after_search(admin_client):
    _ingest_sample(admin_client)
    search = admin_client.get("/api/analytics/investigation/search?field=transaction_id&query=TXN1&lookback_hours=24").json()
    flow_id = search["results"][0]["flow_id"]
    resp = admin_client.get(f"/api/analytics/investigation/{flow_id}?lookback_hours=24")
    assert resp.status_code == 200
    assert resp.json()["flow_id"] == flow_id


# ---------------------------------------------------------------------------
# Security / data quality
# ---------------------------------------------------------------------------

def test_security_quality_shape(admin_client):
    resp = admin_client.get("/api/analytics/security-quality")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("scorecard", "correlation_quality_breakdown", "field_consistency_exceptions", "sensitive_data_findings", "repeated_attempts", "incomplete_flows"):
        assert key in body


def test_security_quality_never_leaks_raw_sample_content(admin_client):
    _ingest_sample(admin_client)
    resp = admin_client.get("/api/analytics/security-quality?lookback_hours=24")
    assert resp.status_code == 200
    # the sample log has no injected secrets, but this guards the contract:
    # sensitive_data_findings must never carry a "raw_value"-shaped key
    for finding in resp.json()["sensitive_data_findings"]:
        assert "raw_value" not in finding
        assert "value" not in finding
