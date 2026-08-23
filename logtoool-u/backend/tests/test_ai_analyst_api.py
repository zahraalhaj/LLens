"""
API-level tests for /api/ai-analyst/* -- same fixture pattern as
test_analytics_api.py. Proves the full stack (auth, routing, pipeline
execution, and audit-log persistence) works end-to-end. The LLM itself is
swapped out via FastAPI's dependency_overrides (not a live Ollama call) --
backend/tests/test_ai_analyst.py already covers the orchestration/
validation logic in isolation with mocked generate_json() calls.
"""
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.analysis.ai_analyst_schema import AnalystAnswer, Confidence

ALL_CACHED_DEPS = [
    "get_db", "get_profile_manager", "get_auth_service", "get_ollama_client", "get_rate_limiter",
    "get_ingestion_engine", "get_explainer", "get_chat_assistant", "get_profile_generator",
    "get_email_dispatcher", "get_dedup_engine", "get_alert_rule_manager", "get_alert_processor",
    "get_remote_machine_service", "get_ai_analyst_assistant", "get_ai_analyst_audit_log",
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

    app.dependency_overrides.clear()
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
    get_auth_service().create_user("member1", "memberpass123", role="member")
    client.post("/api/auth/login", json={"username": "member1", "password": "memberpass123"})
    client.post("/api/auth/change-password", json={"current_password": "memberpass123", "new_password": "memberpass123-real"})
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


class _StubAssistant:
    def __init__(self, answer: AnalystAnswer):
        self._answer = answer
        self.client = type("C", (), {"model": "qwen3:8b"})()

    def ask(self, question, bundle):
        return self._answer


def _override_assistant(answer: AnalystAnswer):
    from backend.api.deps import get_ai_analyst_assistant
    from backend.api.main import app

    app.dependency_overrides[get_ai_analyst_assistant] = lambda: _StubAssistant(answer)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_ask_requires_authentication(client):
    resp = client.post("/api/ai-analyst/ask", json={"question": "Is V+ having issues?"})
    assert resp.status_code == 401


def test_audit_log_requires_admin(member_client):
    resp = member_client.get("/api/ai-analyst/audit-log")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Happy path + audit logging
# ---------------------------------------------------------------------------


def test_ask_returns_stubbed_answer_and_records_audit_entry(admin_client):
    _ingest_sample(admin_client)
    stub_answer = AnalystAnswer(
        question="What happened to transaction TXN1?",
        answer="Transaction TXN1 completed successfully.",
        confidence=Confidence.HIGH,
        tool_used="lookup_transaction",
        tool_parameters={"field": "transaction_id", "value": "TXN1"},
        metrics={"tool": "lookup_transaction", "match_count": 1},
        ai_available=True,
        ai_status_message="AI answer generated and validated successfully.",
    )
    _override_assistant(stub_answer)

    resp = admin_client.post("/api/ai-analyst/ask", json={"question": "What happened to transaction TXN1?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Transaction TXN1 completed successfully."
    assert body["tool_used"] == "lookup_transaction"
    assert body["confidence"] == "HIGH"

    audit_resp = admin_client.get("/api/ai-analyst/audit-log")
    assert audit_resp.status_code == 200
    entries = audit_resp.json()["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["username"] == "admin"
    assert entry["tool_used"] == "lookup_transaction"
    assert entry["confidence"] == "HIGH"
    assert "cardinal_stepup_oob_log" in entry["data_sources_used"]
    assert entry["model_name"] == "ollama:qwen3:8b"
    assert entry["engine_version"]
    assert entry["question"] == "What happened to transaction TXN1?"


def test_audit_log_redacts_sensitive_looking_question_text(admin_client):
    stub_answer = AnalystAnswer(
        question="contact",
        answer="No match found.",
        confidence=Confidence.LOW,
        unsupported=True,
        limitation_explanation="No supported tool matches this question.",
        ai_available=True,
    )
    _override_assistant(stub_answer)

    sensitive_question = "What happened for card 4111111111111111 and email jane.doe@example.com?"
    resp = admin_client.post("/api/ai-analyst/ask", json={"question": sensitive_question})
    assert resp.status_code == 200

    audit_resp = admin_client.get("/api/ai-analyst/audit-log")
    entry = audit_resp.json()["entries"][0]
    assert "4111111111111111" not in entry["question"]
    assert "jane.doe@example.com" not in entry["question"]
    assert "[REDACTED_NUMBER]" in entry["question"]
    assert "[REDACTED_EMAIL]" in entry["question"]
