"""
AI Analyst audit log model. Shares the same declarative Base as everything
else -- one SQLite file, Base.metadata.create_all() picks up this table too.
"""
from sqlalchemy import Column, Integer, String, Text

from backend.core.store import Base


class AIAnalystAuditLogModel(Base):
    """One row per AI Analyst question -- durable, not in-memory, so
    investigations are reviewable after a restart (see
    backend/llm/audit.py's module docstring)."""

    __tablename__ = "ai_analyst_audit_log"

    audit_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    username = Column(String, nullable=False)
    asked_at = Column(String, nullable=False)

    question_redacted = Column(Text, nullable=False)
    tool_used = Column(String, nullable=True)
    tool_parameters = Column(Text, nullable=True)  # JSON
    data_sources_used = Column(Text, nullable=False)  # JSON list of log families included in the pipeline run
    result_summary_redacted = Column(Text, nullable=False)
    confidence = Column(String, nullable=True)

    model_name = Column(String, nullable=False)  # e.g. "ollama:qwen3:8b"
    engine_version = Column(String, nullable=False)  # backend.analysis.pipeline.ENGINE_VERSION at answer time
    ai_available = Column(Integer, nullable=False)
