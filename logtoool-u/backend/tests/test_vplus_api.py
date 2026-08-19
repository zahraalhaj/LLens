"""
API-level tests for /api/vplus/* -- self-contained fixture pattern, same
approach as test_machines_api.py / test_alerts_api.py.
"""
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ALL_CACHED_DEPS = [
    "get_db", "get_profile_manager", "get_auth_service", "get_ollama_client", "get_rate_limiter",
    "get_ingestion_engine", "get_explainer", "get_chat_assistant", "get_profile_generator",
    "get_email_dispatcher", "get_dedup_engine", "get_alert_rule_manager", "get_alert_processor",
    "get_remote_machine_service",
]


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


# Timestamps are relative to real "now" rather than hardcoded -- the
# availability/transactions endpoints default to a 24h lookback window
# compared against datetime.now(), so a fixed calendar date drifts out of
# range and starts failing purely from wall-clock time passing.
_T0 = datetime.now(timezone.utc) - timedelta(hours=1)
_T1 = _T0 + timedelta(seconds=3)

SAMPLE_LOG = (
    f'{_T0.strftime("%-m/%-d/%Y %I:%M:%S %p")} Log Tracker No: SU12345 => Stepup Responce to Netcetra: '
    '{"TransactionId": "TXN001", "IssuerId": "ISS01", "Status": "SUCCESS", '
    '"MerchantInfo": {"MerchantName": "Acme Store"}}\n'
    f'{_T1.strftime("%-m/%-d/%Y %I:%M:%S %p")} Log Tracker No: SU12345 => StepupCall V+ Input Message details here\n'
)


def test_unauthenticated_request_rejected(client):
    assert client.get("/api/vplus/availability").status_code == 401


def test_availability_endpoint_with_no_data(admin_client):
    resp = admin_client.get("/api/vplus/availability")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


def test_availability_endpoint_after_ingestion(admin_client):
    files = {"file": ("afs.log", io.BytesIO(SAMPLE_LOG.encode()), "application/octet-stream")}
    admin_client.post("/api/logs/upload", files=files)

    resp = admin_client.get("/api/vplus/availability")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("healthy", "down")  # real computed status, not a stub
    assert body["total_inputs_analyzed"] >= 1


def test_response_times_endpoint_query_params(admin_client):
    resp = admin_client.get("/api/vplus/response-times?expected_response_ms=1000&lookback_hours=1")
    assert resp.status_code == 200
    assert resp.json()["expected_response_ms"] == 1000


def test_sms_analysis_endpoint(admin_client):
    resp = admin_client.get("/api/vplus/sms-analysis")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


def test_investigation_summary_endpoint_combines_all_reports(admin_client):
    files = {"file": ("afs.log", io.BytesIO(SAMPLE_LOG.encode()), "application/octet-stream")}
    admin_client.post("/api/logs/upload", files=files)

    resp = admin_client.get("/api/vplus/investigation-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "investigation_summary" in body
    assert "availability" in body
    assert "response_times" in body
    assert "sms_analysis" in body


def test_invalid_lookback_hours_rejected(admin_client):
    resp = admin_client.get("/api/vplus/availability?lookback_hours=0")
    assert resp.status_code == 422  # below the ge=1 constraint


def test_transactions_endpoint_with_no_data(admin_client):
    resp = admin_client.get("/api/vplus/transactions")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


def test_transactions_endpoint_after_ingestion(admin_client):
    files = {"file": ("afs.log", io.BytesIO(SAMPLE_LOG.encode()), "application/octet-stream")}
    admin_client.post("/api/logs/upload", files=files)

    resp = admin_client.get("/api/vplus/transactions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["total_transactions"] == 1
    assert body["issuer_counts"] == {"ISS01": 1}
    assert body["status_counts"] == {"SUCCESS": 1}


def test_investigation_summary_includes_transaction_breakdown(admin_client):
    files = {"file": ("afs.log", io.BytesIO(SAMPLE_LOG.encode()), "application/octet-stream")}
    admin_client.post("/api/logs/upload", files=files)

    resp = admin_client.get("/api/vplus/investigation-summary")
    assert resp.status_code == 200
    assert "transaction_breakdown" in resp.json()
