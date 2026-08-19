"""
API-level tests for /api/otp-processor/* -- same fixture pattern as
test_vplus_api.py.
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
# summary endpoint defaults to a 24h lookback window compared against
# datetime.now(), so a fixed calendar date drifts out of range and starts
# failing purely from wall-clock time passing.
_T0 = datetime.now(timezone.utc) - timedelta(hours=1)
_T1 = _T0 + timedelta(seconds=1)
_T2 = _T0 + timedelta(seconds=5)

SAMPLE_LOG = (
    f'{_T0.strftime("%-m/%-d/%Y %I:%M:%S %p")} : Msg Received-- &lt;Msg&gt;&lt;Header&gt;&lt;mtrackingid&gt;IA11111&lt;/mtrackingid&gt;'
    "&lt;Org&gt;ABCBANK&lt;/Org&gt;&lt;Mobile&gt;96170000000&lt;/Mobile&gt;&lt;/Header&gt;&lt;Body&gt;&lt;OTP&gt;654321&lt;/OTP&gt;"
    "&lt;MerchantName&gt;Book &amp; Bean&lt;/MerchantName&gt;&lt;/Body&gt;&lt;/Msg&gt;\n"
    f'{_T1.strftime("%-m/%-d/%Y %I:%M:%S %p")} : Message for Queue=mq-oab-otp-in-push\n'
    f'{_T2.strftime("%-m/%-d/%Y %I:%M:%S %p")} : OTP Processed Successfully for Tracker IA11111\n'
)


def test_unauthenticated_request_rejected(client):
    assert client.get("/api/otp-processor/summary").status_code == 401


def test_summary_endpoint_with_no_data(admin_client):
    resp = admin_client.get("/api/otp-processor/summary")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_data"


def test_summary_endpoint_after_ingestion(admin_client):
    files = {"file": ("otp.log", io.BytesIO(SAMPLE_LOG.encode()), "application/octet-stream")}
    resp = admin_client.post("/api/logs/upload", files=files)
    assert resp.status_code == 200

    resp = admin_client.get("/api/otp-processor/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["total_records"] == 1
    assert body["by_queue"] == {"mq-oab-otp-in-push": 1}
    assert body["by_org"] == {"ABCBANK": 1}
    assert body["top_merchants"] == {"Book & Bean": 1}
    assert body["otp_processed_count"] == 1


def test_invalid_lookback_hours_rejected(admin_client):
    resp = admin_client.get("/api/otp-processor/summary?lookback_hours=0")
    assert resp.status_code == 422  # below the ge=1 constraint
