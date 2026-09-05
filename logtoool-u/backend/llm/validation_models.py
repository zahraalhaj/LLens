"""
LLM validation-outcome model. Shares the same declarative Base as
everything else -- one SQLite file, Base.metadata.create_all() picks up
this table too (same pattern as backend/llm/audit_models.py).
"""
from sqlalchemy import Column, Integer, String, Text

from backend.core.store import Base


class LLMValidationEventModel(Base):
    """One row per validation pass over one model response.

    Durable rather than in-memory counters: the point of this table is to
    answer "is the model getting worse than it was last week", which needs
    history that survives a restart -- an in-process counter resets exactly
    when someone restarts the server to investigate the degradation.
    """

    __tablename__ = "llm_validation_events"

    event_id = Column(String, primary_key=True)
    occurred_at = Column(String, nullable=False, index=True)

    # Which validation gate ran -- see RejectionReason/SURFACES in
    # backend/llm/validation_metrics.py.
    surface = Column(String, nullable=False, index=True)
    model_name = Column(String, nullable=False)

    # Item-level: how much of a multi-statement response survived.
    items_total = Column(Integer, nullable=False)
    items_accepted = Column(Integer, nullable=False)
    items_rejected = Column(Integer, nullable=False)

    # Response-level: 1 when the response as a whole was unusable (bad
    # JSON, invented tool, unsafe SQL) rather than partially trimmed.
    response_rejected = Column(Integer, nullable=False)

    reason_counts = Column(Text, nullable=False)  # JSON {reason: count}
    # A short, redacted excerpt of ONE rejected item, for diagnosing what
    # the model actually got wrong. Never the full response.
    sample_redacted = Column(Text, nullable=True)
