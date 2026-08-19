"""
API-level tests for /api/alerts/* -- self-contained fixture pattern, same
approach as test_machines_api.py.
"""
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


@pytest.fixture
def member_client(client):
    from backend.api.deps import get_auth_service
    get_auth_service().create_user("bob", "bobpass123", role="member")
    client.post("/api/auth/login", json={"username": "bob", "password": "bobpass123"})
    client.post("/api/auth/change-password", json={"current_password": "bobpass123", "new_password": "bobpass123-real"})
    return client


def test_member_can_list_rules(member_client):
    resp = member_client.get("/api/alerts/rules")
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert "CRITICAL Event Immediate Alert" in names  # defaults seeded


def test_member_cannot_create_rule(member_client):
    resp = member_client.post("/api/alerts/rules", json={"name": "x", "min_level": "ERROR", "mode": "immediate"})
    assert resp.status_code == 403


def test_admin_can_create_rule(admin_client):
    resp = admin_client.post(
        "/api/alerts/rules",
        json={"name": "payments-critical", "min_level": "CRITICAL", "mode": "immediate", "source_system_filter": "payments"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "payments-critical"
    assert body["source_system_filter"] == "payments"


def test_create_rule_rejects_invalid_level(admin_client):
    resp = admin_client.post("/api/alerts/rules", json={"name": "bad", "min_level": "NOT_A_LEVEL", "mode": "immediate"})
    assert resp.status_code == 400


def test_create_rule_rejects_duplicate_name(admin_client):
    admin_client.post("/api/alerts/rules", json={"name": "dup", "min_level": "ERROR", "mode": "immediate"})
    resp = admin_client.post("/api/alerts/rules", json={"name": "dup", "min_level": "WARN", "mode": "digest"})
    assert resp.status_code == 409


def test_member_cannot_update_or_delete_rule(admin_client):
    created = admin_client.post("/api/alerts/rules", json={"name": "target", "min_level": "ERROR", "mode": "immediate"}).json()

    from backend.api.deps import get_auth_service
    get_auth_service().create_user("bob", "bobpass123", role="member")
    from backend.api.main import app
    bob_client = TestClient(app)
    bob_client.post("/api/auth/login", json={"username": "bob", "password": "bobpass123"})
    bob_client.post("/api/auth/change-password", json={"current_password": "bobpass123", "new_password": "bobpass123-real"})

    assert bob_client.put(f"/api/alerts/rules/{created['rule_id']}", json={"enabled": False}).status_code == 403
    assert bob_client.delete(f"/api/alerts/rules/{created['rule_id']}").status_code == 403


def test_admin_can_disable_and_delete_rule(admin_client):
    created = admin_client.post("/api/alerts/rules", json={"name": "temp", "min_level": "ERROR", "mode": "immediate"}).json()
    updated = admin_client.put(f"/api/alerts/rules/{created['rule_id']}", json={"enabled": False})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    deleted = admin_client.delete(f"/api/alerts/rules/{created['rule_id']}")
    assert deleted.status_code == 200
    assert admin_client.get(f"/api/alerts/rules/{created['rule_id']}").status_code == 404


def test_alert_fires_on_upload_and_appears_in_history(admin_client, tmp_path):
    import io
    content = b'{"timestamp": "2026-08-15T10:00:00Z", "level": "CRITICAL", "service": "payments", "message": "payment gateway down"}\n'
    files = {"file": ("t.jsonl", io.BytesIO(content), "application/octet-stream")}
    resp = admin_client.post("/api/logs/upload", files=files)
    assert resp.status_code == 200

    history = admin_client.get("/api/alerts/history").json()
    assert history["total"] >= 1
    assert any(e["rule_name"] == "CRITICAL Event Immediate Alert" for e in history["entries"])


def test_member_can_view_history(admin_client):
    import io
    content = b'{"timestamp": "2026-08-15T10:00:00Z", "level": "CRITICAL", "service": "x", "message": "boom"}\n'
    files = {"file": ("t.jsonl", io.BytesIO(content), "application/octet-stream")}
    admin_client.post("/api/logs/upload", files=files)

    from backend.api.deps import get_auth_service
    get_auth_service().create_user("carol", "carolpass123", role="member")
    from backend.api.main import app
    carol_client = TestClient(app)
    carol_client.post("/api/auth/login", json={"username": "carol", "password": "carolpass123"})
    carol_client.post("/api/auth/change-password", json={"current_password": "carolpass123", "new_password": "carolpass123-real"})

    resp = carol_client.get("/api/alerts/history")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_member_cannot_reset_dedup(member_client):
    assert member_client.post("/api/alerts/dedup/reset").status_code == 403


def test_admin_can_reset_dedup(admin_client):
    assert admin_client.post("/api/alerts/dedup/reset").status_code == 200


def test_manual_test_alert_recorded_in_history(admin_client):
    resp = admin_client.post("/api/alerts/test", json={})
    assert resp.status_code == 200
    history = admin_client.get("/api/alerts/history").json()
    assert any(e["rule_name"] == "(manual test)" for e in history["entries"])
