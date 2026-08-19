"""
End-to-end tests against the FastAPI app itself (not just the underlying
services), using a fresh temp DB per test.

Isolation approach: deps.py's service singletons are lru_cache'd functions
that read from the shared `settings` object. We monkeypatch attributes on
that *same object* (not reload the module -- a reload would create a new
settings object that deps.py's already-bound `from ... import settings`
wouldn't see) and clear every lru_cache so the next call rebuilds services
against the patched paths.
"""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ALL_CACHED_DEPS = [
    "get_db",
    "get_profile_manager",
    "get_auth_service",
    "get_ollama_client",
    "get_rate_limiter",
    "get_ingestion_engine",
    "get_explainer",
    "get_chat_assistant",
    "get_profile_generator",
    "get_email_dispatcher",
    "get_dedup_engine",
    "get_alert_processor",
    "get_remote_machine_service",
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.api import deps
    from backend.api.config import settings

    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "profiles_dir", str(tmp_path / "profiles"))
    monkeypatch.setattr(settings, "cookie_secure", False)  # TestClient has no https
    # Deliberately unreachable -- tests that touch AI routes should see a
    # clean "unavailable" response, not accidentally hit a real local Ollama.
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
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "adminpass123"})
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True
    # New accounts always start with a must-change password (see
    # test_forced_password_change.py for the dedicated coverage of that
    # flow) -- complete it here so the rest of these tests can exercise
    # everything else as a normal, fully-active session.
    resp = client.post(
        "/api/auth/change-password", json={"current_password": "adminpass123", "new_password": "adminpass123-real"}
    )
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is False
    return client


@pytest.fixture
def member_client(client):
    from backend.api.deps import get_auth_service
    get_auth_service().create_user("bob", "bobpass123", role="member")
    resp = client.post("/api/auth/login", json={"username": "bob", "password": "bobpass123"})
    assert resp.status_code == 200
    resp = client.post(
        "/api/auth/change-password", json={"current_password": "bobpass123", "new_password": "bobpass123-real"}
    )
    assert resp.status_code == 200
    return client


def test_health_is_public(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_protected_route_requires_auth(client):
    resp = client.get("/api/logs/events")
    assert resp.status_code == 401


def test_login_wrong_password_fails(client):
    from backend.api.deps import get_auth_service
    get_auth_service().create_user("admin", "correctpw", role="admin")
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_lockout_after_repeated_failures(client):
    from backend.api.deps import get_auth_service
    get_auth_service().create_user("admin", "correctpw", role="admin")
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "correctpw"})
    assert resp.status_code == 429


def test_member_cannot_clear_logs(member_client):
    resp = member_client.delete("/api/logs/clear")
    assert resp.status_code == 403


def test_admin_can_clear_logs(admin_client):
    resp = admin_client.delete("/api/logs/clear")
    assert resp.status_code == 200


def test_admin_cannot_delete_own_account(admin_client):
    me = admin_client.get("/api/auth/me").json()
    resp = admin_client.delete(f"/api/users/{me['user_id']}")
    assert resp.status_code == 400


def test_member_cannot_manage_users(member_client):
    resp = member_client.get("/api/users")
    assert resp.status_code == 403


def test_upload_and_query_roundtrip(admin_client):
    content = b'{"timestamp": "2026-08-05T14:30:15Z", "level": "ERROR", "service": "billing", "message": "charge failed"}\n'
    files = {"file": ("test.jsonl", io.BytesIO(content), "application/octet-stream")}
    resp = admin_client.post("/api/logs/upload", files=files)
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["parsed_events_count"] == 1

    events = admin_client.get("/api/logs/events").json()
    assert events["total"] == 1
    assert events["events"][0]["level"] == "ERROR"
    # "Application JSON Log" maps the "service" key to component_field, not
    # source_system_field (only the syslog profile uses source_system_field,
    # for the extracted hostname) -- component is the correct field to check.
    assert events["events"][0]["component"] == "billing"


def test_ingest_directory_requires_admin(member_client, tmp_path):
    resp = member_client.post("/api/logs/ingest-directory", json={"path": str(tmp_path)})
    assert resp.status_code == 403


def test_ingest_directory_rejects_nonexistent_path(admin_client):
    resp = admin_client.post("/api/logs/ingest-directory", json={"path": "/definitely/not/a/real/path"})
    assert resp.status_code == 400


def test_ingest_directory_rejects_a_file_path(admin_client, tmp_path):
    f = tmp_path / "not_a_directory.log"
    f.write_text("hello")
    resp = admin_client.post("/api/logs/ingest-directory", json={"path": str(f)})
    assert resp.status_code == 400


def test_ingest_directory_ingests_recognized_files_and_skips_others(admin_client, tmp_path):
    # Deliberately separate from tmp_path itself -- the test fixture already
    # uses tmp_path for the app's own profiles/data dirs, so scanning
    # tmp_path directly would also pick up the app's own profile JSON files.
    logs_dir = tmp_path / "logs_to_ingest"
    logs_dir.mkdir()
    (logs_dir / "app.jsonl").write_text(
        '{"timestamp": "2026-08-05T14:30:15Z", "level": "ERROR", "service": "billing", "message": "boom"}\n'
    )
    (logs_dir / "readme.md").write_text("this is not a log file")
    subdir = logs_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.jsonl").write_text(
        '{"timestamp": "2026-08-05T14:31:00Z", "level": "INFO", "service": "billing", "message": "ok"}\n'
    )

    resp = admin_client.post("/api/logs/ingest-directory", json={"path": str(logs_dir), "recursive": True})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["ingested"]) == 2
    assert any("readme.md" in s for s in body["skipped_unrecognized_extension"])
    assert body["errors"] == []

    events = admin_client.get("/api/logs/events").json()
    assert events["total"] == 2


def test_ingest_directory_nonrecursive_skips_subdirectories(admin_client, tmp_path):
    logs_dir = tmp_path / "logs_to_ingest2"
    logs_dir.mkdir()
    (logs_dir / "top.jsonl").write_text(
        '{"timestamp": "2026-08-05T14:30:15Z", "level": "INFO", "service": "x", "message": "top"}\n'
    )
    subdir = logs_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.jsonl").write_text(
        '{"timestamp": "2026-08-05T14:31:00Z", "level": "INFO", "service": "x", "message": "nested"}\n'
    )

    resp = admin_client.post("/api/logs/ingest-directory", json={"path": str(logs_dir), "recursive": False})
    assert resp.status_code == 200
    assert len(resp.json()["ingested"]) == 1


def test_profiling_reports_honest_method_label(admin_client):
    resp = admin_client.get("/api/logs/profiling")
    assert resp.status_code == 200
    body = resp.json()
    assert body["method"] == "heuristic_zscore"
    assert "not a trained machine learning model" in body["description"]


def test_profiles_list_returns_seven_defaults_plus_eight_custom(admin_client):
    resp = admin_client.get("/api/profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    assert len(profiles) == 15
    custom = [p for p in profiles if p["type"] == "custom"]
    assert len(custom) == 8


def test_ai_explain_returns_503_when_ollama_unavailable(admin_client):
    content = b'{"timestamp": "2026-08-05T14:30:15Z", "level": "ERROR", "service": "billing", "message": "boom"}\n'
    files = {"file": ("t.jsonl", io.BytesIO(content), "application/octet-stream")}
    admin_client.post("/api/logs/upload", files=files)
    event_id = admin_client.get("/api/logs/events").json()["events"][0]["event_id"]

    resp = admin_client.post("/api/ai/explain", json={"event_id": event_id})
    assert resp.status_code == 503


def test_logout_invalidates_session(admin_client):
    assert admin_client.get("/api/auth/me").status_code == 200
    admin_client.post("/api/auth/logout")
    assert admin_client.get("/api/auth/me").status_code == 401


def test_new_user_blocked_from_protected_routes_until_password_changed(admin_client):
    admin_client.post("/api/users", json={"username": "nina", "password": "ninapass123", "role": "member"})
    from backend.api.main import app
    nina_client = TestClient(app)
    login_resp = nina_client.post("/api/auth/login", json={"username": "nina", "password": "ninapass123"})
    assert login_resp.status_code == 200
    assert login_resp.json()["must_change_password"] is True

    # Blocked from ordinary routes even though the login itself succeeded.
    assert nina_client.get("/api/logs/events").status_code == 403
    assert nina_client.get("/api/logs/events").json()["detail"] == "password_change_required"

    # /me still works (frontend needs it to detect the pending state).
    assert nina_client.get("/api/auth/me").status_code == 200

    # Wrong current password is rejected.
    bad = nina_client.post(
        "/api/auth/change-password", json={"current_password": "wrong", "new_password": "ninas-real-password"}
    )
    assert bad.status_code == 401

    # Correct flow clears the gate.
    ok = nina_client.post(
        "/api/auth/change-password", json={"current_password": "ninapass123", "new_password": "ninas-real-password"}
    )
    assert ok.status_code == 200
    assert ok.json()["must_change_password"] is False
    assert nina_client.get("/api/logs/events").status_code == 200
    create_resp = admin_client.post(
        "/api/users", json={"username": "carol", "password": "carolpass1", "role": "member"}
    )
    assert create_resp.status_code == 200
    user_id = create_resp.json()["user_id"]

    from backend.api.main import app
    carol_client = TestClient(app)
    login_resp = carol_client.post("/api/auth/login", json={"username": "carol", "password": "carolpass1"})
    assert login_resp.status_code == 200
    assert carol_client.get("/api/auth/me").status_code == 200

    admin_client.patch(f"/api/users/{user_id}/active", json={"is_active": False})
    assert carol_client.get("/api/auth/me").status_code == 401
