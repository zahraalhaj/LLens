"""
API-level tests for /api/machines/* -- reuses the same client/admin_client/
member_client fixture pattern as test_api.py (duplicated rather than
imported, since pytest fixtures from other files aren't trivially
shareable without a conftest.py, and this keeps the new test file
self-contained).
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.remote import crypto
from backend.remote.ssh_client import RemoteFileInfo

ALL_CACHED_DEPS = [
    "get_db", "get_profile_manager", "get_auth_service", "get_ollama_client", "get_rate_limiter",
    "get_ingestion_engine", "get_explainer", "get_chat_assistant", "get_profile_generator",
    "get_email_dispatcher", "get_dedup_engine", "get_alert_processor", "get_remote_machine_service",
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.api import deps
    from backend.api.config import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "profiles_dir", str(tmp_path / "profiles"))
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "ollama_url", "http://localhost:1")
    monkeypatch.setenv(crypto.ENV_VAR_NAME, crypto.generate_key())

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


@pytest.fixture
def member_client(client):
    from backend.api.deps import get_auth_service
    get_auth_service().create_user("bob", "bobpass123", role="member")
    client.post("/api/auth/login", json={"username": "bob", "password": "bobpass123"})
    client.post("/api/auth/change-password", json={"current_password": "bobpass123", "new_password": "bobpass123-real"})
    return client


MACHINE_PAYLOAD = {
    "label": "prod-db-01",
    "host": "10.0.0.5",
    "port": 22,
    "username": "loguser",
    "auth_type": "password",
    "secret": "hunter2",
    "remote_directory": "/var/log/myapp",
    "recursive": True,
    "poll_interval_minutes": 15,
}


def test_member_cannot_list_machines(member_client):
    assert member_client.get("/api/machines").status_code == 403


def test_member_cannot_create_machine(member_client):
    resp = member_client.post("/api/machines", json=MACHINE_PAYLOAD)
    assert resp.status_code == 403


def test_admin_can_create_and_list_machines(admin_client):
    resp = admin_client.post("/api/machines", json=MACHINE_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "prod-db-01"
    assert "secret" not in body
    assert "encrypted_secret" not in body

    listed = admin_client.get("/api/machines").json()
    assert len(listed) == 1


def test_create_machine_rejects_invalid_auth_type(admin_client):
    bad = dict(MACHINE_PAYLOAD, auth_type="carrier_pigeon")
    resp = admin_client.post("/api/machines", json=bad)
    assert resp.status_code == 400


def test_admin_can_delete_machine(admin_client):
    created = admin_client.post("/api/machines", json=MACHINE_PAYLOAD).json()
    resp = admin_client.delete(f"/api/machines/{created['machine_id']}")
    assert resp.status_code == 200
    assert admin_client.get(f"/api/machines/{created['machine_id']}").status_code == 404


def test_get_nonexistent_machine_404s(admin_client):
    assert admin_client.get("/api/machines/does-not-exist").status_code == 404


@patch("backend.remote.service.ssh_client.test_connection")
def test_test_connection_endpoint(mock_test, admin_client):
    mock_test.return_value = "fingerprint123"
    created = admin_client.post("/api/machines", json=MACHINE_PAYLOAD).json()
    resp = admin_client.post(f"/api/machines/{created['machine_id']}/test-connection")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@patch("backend.remote.service.ssh_client.fetch_remote_file")
@patch("backend.remote.service.ssh_client.list_remote_log_files")
def test_poll_now_endpoint_ingests_and_returns_summary(mock_list, mock_fetch, admin_client):
    content = b'{"timestamp": "2026-08-10T10:00:00Z", "level": "ERROR", "service": "billing", "message": "boom"}\n'
    mock_list.return_value = ([RemoteFileInfo(path="/var/log/myapp/app.log", mtime=1000.0, size=len(content))], "fp1")
    mock_fetch.return_value = content

    created = admin_client.post("/api/machines", json=MACHINE_PAYLOAD).json()
    resp = admin_client.post(f"/api/machines/{created['machine_id']}/poll-now")
    assert resp.status_code == 200
    body = resp.json()
    assert body["files_ingested"] == 1
    assert body["total_events_ingested"] == 1

    events = admin_client.get("/api/logs/events").json()
    assert events["total"] == 1


def test_member_cannot_poll_or_test_connection(admin_client):
    created = admin_client.post("/api/machines", json=MACHINE_PAYLOAD).json()

    # member_client and admin_client would share the same TestClient/cookie
    # jar if both were requested as fixtures on this test (whichever
    # fixture's login ran last would win) -- a fresh, separate client
    # avoids that entirely.
    from backend.api.deps import get_auth_service
    get_auth_service().create_user("bob", "bobpass123", role="member")
    from backend.api.main import app
    bob_client = TestClient(app)
    bob_client.post("/api/auth/login", json={"username": "bob", "password": "bobpass123"})
    bob_client.post("/api/auth/change-password", json={"current_password": "bobpass123", "new_password": "bobpass123-real"})

    assert bob_client.post(f"/api/machines/{created['machine_id']}/poll-now").status_code == 403
    assert bob_client.post(f"/api/machines/{created['machine_id']}/test-connection").status_code == 403
