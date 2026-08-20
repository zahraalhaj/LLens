"""
API-level tests for /api/cardinal/* -- same fixture pattern as
test_otp_processor_api.py. Uploads force the profile via `profile_name`
since this format's detect() ties with several siblings on generic
"Log Tracker No" content -- forcing keeps the test deterministic.
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


def test_unauthenticated_request_rejected(client):
    assert client.get("/api/cardinal/summary").status_code == 401


def test_summary_endpoint_with_no_data(admin_client):
    resp = admin_client.get("/api/cardinal/summary")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


def test_summary_endpoint_after_ingestion(admin_client):
    files = {"file": ("cardinal.log", io.BytesIO(SAMPLE_LOG.encode()), "application/octet-stream")}
    resp = admin_client.post(f"/api/logs/upload?profile_name={quote(PROFILE_NAME)}", files=files)
    assert resp.status_code == 200

    resp = admin_client.get("/api/cardinal/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["total_flows"] == 1
    assert body["by_issuer"] == {"ISS1": 1}
    assert body["by_status"] == {"SUCCESS": 1}
    assert body["top_merchants"] == {"Store A": 1}


def test_invalid_lookback_hours_rejected(admin_client):
    resp = admin_client.get("/api/cardinal/summary?lookback_hours=0")
    assert resp.status_code == 422
