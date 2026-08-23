"""
AI Analyst audit log -- Phase 11 of the LLens Multi-Log Analysis Strategy.

Persists every AI Analyst question to its own table (same db file, same
Base metadata as everything else -- same pattern as
backend/alerts/state.py's AlertDeduplicationEngine), so investigations
survive restarts and are reviewable later: who asked what, using which
analytical data sources, what the system answered, and which model/engine
version produced it.

The raw question text and the answer summary are redacted with lightweight
sensitive-data patterns (same categories Phase 9's
backend/analysis/quality.py already scans the analytical model for --
PAN-length numbers, mobile numbers, email addresses) before being
persisted. A user could paste a real card/mobile number into a typed
question by mistake, and this audit table must not become a second place
that raw value ends up stored -- consistent with "all sensitive information
must remain masked" applying to the audit trail too, not just the answers.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.store import Base
from backend.llm.audit_models import AIAnalystAuditLogModel

logger = logging.getLogger("logtool.llm.audit")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PAN_RE = re.compile(r"\b\d{13,19}\b")
_MOBILE_RE = re.compile(r"\+?\d[\d\-\s]{8,14}\d")

_QUESTION_MAX_LEN = 2000
_RESULT_MAX_LEN = 4000
_PARAMS_MAX_LEN = 2000


def redact_sensitive_text(text: Optional[str]) -> str:
    if not text:
        return ""
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = _PAN_RE.sub("[REDACTED_NUMBER]", redacted)
    redacted = _MOBILE_RE.sub("[REDACTED_NUMBER]", redacted)
    return redacted


class AIAnalystAuditLog:
    def __init__(self, db_path: str):
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record(
        self,
        user_id: str,
        username: str,
        question: str,
        tool_used: Optional[str],
        tool_parameters: Dict[str, Any],
        data_sources_used: List[str],
        result_summary: str,
        confidence: Optional[str],
        model_name: str,
        engine_version: str,
        ai_available: bool,
    ) -> str:
        audit_id = str(uuid.uuid4())
        session = self.Session()
        try:
            session.add(
                AIAnalystAuditLogModel(
                    audit_id=audit_id,
                    user_id=user_id,
                    username=username,
                    asked_at=datetime.now(timezone.utc).isoformat(),
                    question_redacted=redact_sensitive_text(question)[:_QUESTION_MAX_LEN],
                    tool_used=tool_used,
                    tool_parameters=json.dumps(tool_parameters, default=str)[:_PARAMS_MAX_LEN],
                    data_sources_used=json.dumps(list(data_sources_used)),
                    result_summary_redacted=redact_sensitive_text(result_summary)[:_RESULT_MAX_LEN],
                    confidence=confidence,
                    model_name=model_name,
                    engine_version=engine_version,
                    ai_available=1 if ai_available else 0,
                )
            )
            session.commit()
            return audit_id
        finally:
            session.close()

    def list_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            rows = (
                session.query(AIAnalystAuditLogModel)
                .order_by(AIAnalystAuditLogModel.asked_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "audit_id": r.audit_id,
                    "user_id": r.user_id,
                    "username": r.username,
                    "asked_at": r.asked_at,
                    "question": r.question_redacted,
                    "tool_used": r.tool_used,
                    "tool_parameters": json.loads(r.tool_parameters) if r.tool_parameters else {},
                    "data_sources_used": json.loads(r.data_sources_used) if r.data_sources_used else [],
                    "result_summary": r.result_summary_redacted,
                    "confidence": r.confidence,
                    "model_name": r.model_name,
                    "engine_version": r.engine_version,
                    "ai_available": bool(r.ai_available),
                }
                for r in rows
            ]
        finally:
            session.close()
