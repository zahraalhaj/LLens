"""
Hallucination / validation monitoring -- observability for the LLM
validation gates.

WHY THIS EXISTS
---------------
Every LLM output in this app passes a validation gate that discards
anything it cannot tie back to the deterministic engine: a narrative
statement citing an event id that does not exist is dropped
(investigate.py's _validate_statements), evidence citing an unknown flow id
is dropped (ai_analyst.py's _validate_evidence), an invented tool name is
treated as "no match", generated SQL that fails the safety check is
refused. That design is correct and stays exactly as it is.

What was missing is that those discards were *only* a logger.warning. The
user-visible answer silently shrinks, and nobody finds out that the model
started fabricating event ids until someone reads the server log by hand.
The rejection rate is the earliest available signal of AI quality
degradation -- it moves before the answers become visibly wrong, because
the gate is still catching the bad output at that point.

So: the gates still discard, exactly as before. They now also count what
they discarded, and why.

WHAT IS RECORDED
----------------
One row per validation pass over one model response, holding the item
counts and a per-reason breakdown. Reasons are grouped into three classes
(REASON_CLASS below), and the distinction is the actually useful part:

  - hallucination   -- the model referenced something that does not exist
                       (event id, source file, flow id, tool). A rising
                       rate here means the model is losing its grounding.
  - policy_violation -- well-formed output that broke a content rule
                       (forbidden claims, calling a pending flow failed,
                       unsafe SQL). Rising means prompt/guardrail drift.
  - malformed_output -- the response wasn't usable JSON/shape at all.
                       Rising usually means a model/version/prompt-format
                       problem rather than reasoning degradation.

Collapsing all three into one "rejection rate" would hide exactly the
distinction an operator needs to act on, so they are kept apart.

THIS MUST NEVER BREAK THE AI PATH. Recording is best-effort: a failure to
write a metrics row is logged and swallowed. A monitoring feature that can
take down the thing it monitors is worse than no monitoring.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.store import Base
from backend.llm.audit import redact_sensitive_text
from backend.llm.validation_models import LLMValidationEventModel

logger = logging.getLogger("logtool.llm.validation_metrics")

_SAMPLE_MAX_LEN = 300


class Surface:
    """The validation gates, named by where they run."""

    ANALYST_TOOL_SELECTION = "analyst_tool_selection"
    ANALYST_NARRATION = "analyst_narration"
    ANALYST_EVIDENCE = "analyst_evidence"
    INVESTIGATE_NARRATIVE = "investigate_narrative"
    CHAT_SQL = "chat_sql"


class RejectionReason:
    """Stable codes for each discard branch that already existed in the
    validation gates. These are persisted, so the strings are an API --
    add new ones, don't rename existing ones."""

    # -- fabricated references (hallucination)
    UNKNOWN_EVENT_ID = "unknown_event_id"
    UNKNOWN_SOURCE_FILE = "unknown_source_file"
    UNKNOWN_FLOW_ID = "unknown_flow_id"
    UNKNOWN_TOOL = "unknown_tool"

    # -- content rules broken by otherwise well-formed output
    FORBIDDEN_CONTENT = "forbidden_content"
    PENDING_TREATED_AS_FAILURE = "pending_treated_as_failure"
    SQL_FAILED_SECURITY_VALIDATION = "sql_failed_security_validation"

    # -- output that wasn't usable in the first place
    MALFORMED_ITEM = "malformed_item"
    INVALID_ROLE_TYPE_OR_EMPTY_TEXT = "invalid_role_type_or_empty_text"
    MALFORMED_JSON_RESPONSE = "malformed_json_response"
    TOOL_PARAMETER_MISMATCH = "tool_parameter_mismatch"


HALLUCINATION = "hallucination"
POLICY_VIOLATION = "policy_violation"
MALFORMED_OUTPUT = "malformed_output"

REASON_CLASS: Dict[str, str] = {
    RejectionReason.UNKNOWN_EVENT_ID: HALLUCINATION,
    RejectionReason.UNKNOWN_SOURCE_FILE: HALLUCINATION,
    RejectionReason.UNKNOWN_FLOW_ID: HALLUCINATION,
    RejectionReason.UNKNOWN_TOOL: HALLUCINATION,
    RejectionReason.FORBIDDEN_CONTENT: POLICY_VIOLATION,
    RejectionReason.PENDING_TREATED_AS_FAILURE: POLICY_VIOLATION,
    RejectionReason.SQL_FAILED_SECURITY_VALIDATION: POLICY_VIOLATION,
    RejectionReason.MALFORMED_ITEM: MALFORMED_OUTPUT,
    RejectionReason.INVALID_ROLE_TYPE_OR_EMPTY_TEXT: MALFORMED_OUTPUT,
    RejectionReason.MALFORMED_JSON_RESPONSE: MALFORMED_OUTPUT,
    RejectionReason.TOOL_PARAMETER_MISMATCH: MALFORMED_OUTPUT,
}

ALL_CLASSES = (HALLUCINATION, POLICY_VIOLATION, MALFORMED_OUTPUT)


@dataclass
class ValidationOutcome:
    """Accumulated by a validation gate as it runs, then handed to
    LLMValidationMetrics.record().

    Deliberately a plain value object with no DB handle: the gates stay
    pure functions of their input (and remain unit-testable without a
    database), and only the caller that already owns a recorder persists
    the result.
    """

    surface: str
    items_total: int = 0
    items_accepted: int = 0
    reason_counts: Dict[str, int] = field(default_factory=dict)
    response_rejected: bool = False
    sample: Optional[str] = None

    def accept(self) -> None:
        self.items_total += 1
        self.items_accepted += 1

    def reject(self, reason: str, sample: Optional[str] = None) -> None:
        self.items_total += 1
        self.reason_counts[reason] = self.reason_counts.get(reason, 0) + 1
        # Keep the FIRST rejected sample, not the last: when a response
        # goes bad it usually goes bad from a point onward, and the first
        # failure is the more diagnostic one.
        if sample and not self.sample:
            self.sample = sample

    def reject_response(self, reason: str, sample: Optional[str] = None) -> None:
        """The response as a whole was unusable -- not a trimmed item."""
        self.response_rejected = True
        self.reject(reason, sample)

    @property
    def items_rejected(self) -> int:
        return self.items_total - self.items_accepted

    @property
    def has_rejections(self) -> bool:
        return self.items_rejected > 0 or self.response_rejected


class LLMValidationMetrics:
    """Durable recorder + reader for validation outcomes.

    Same construction pattern as backend/llm/audit.py's AIAnalystAuditLog:
    its own engine over the same SQLite file, table created on init.
    """

    def __init__(self, db_path: str):
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def record(self, outcome: ValidationOutcome, model_name: str) -> Optional[str]:
        """Best-effort. Returns the row id, or None if nothing was worth
        recording or the write failed.

        A clean pass (no rejections) IS recorded: the rejection RATE needs
        a denominator, and "20 rejections" means nothing without knowing
        whether that was out of 25 responses or 25,000.
        """
        try:
            event_id = str(uuid.uuid4())
            session = self.Session()
            try:
                session.add(
                    LLMValidationEventModel(
                        event_id=event_id,
                        occurred_at=datetime.now(timezone.utc).isoformat(),
                        surface=outcome.surface,
                        model_name=model_name,
                        items_total=outcome.items_total,
                        items_accepted=outcome.items_accepted,
                        items_rejected=outcome.items_rejected,
                        response_rejected=1 if outcome.response_rejected else 0,
                        reason_counts=json.dumps(outcome.reason_counts),
                        # The sample is model output that can quote log
                        # content, so it goes through the same redaction the
                        # audit log uses -- this table must not become a
                        # second place a raw PAN/mobile/email ends up stored.
                        sample_redacted=(
                            redact_sensitive_text(outcome.sample)[:_SAMPLE_MAX_LEN] if outcome.sample else None
                        ),
                    )
                )
                session.commit()
                return event_id
            finally:
                session.close()
        except Exception:
            # Never let monitoring break the thing it monitors.
            logger.exception("Failed to record LLM validation outcome for surface %s", outcome.surface)
            return None

    def _rows_since(self, session, cutoff_iso: str):
        return (
            session.query(LLMValidationEventModel)
            .filter(LLMValidationEventModel.occurred_at >= cutoff_iso)
            .order_by(LLMValidationEventModel.occurred_at.desc())
            .all()
        )

    def summary(self, lookback_hours: int = 24 * 7) -> Dict[str, Any]:
        """Rejection rates over a window, broken down by reason, class and
        surface, plus a daily series so a TREND is visible -- a single
        aggregate number can't distinguish "always been 3%" from "was 0%
        until Tuesday", and only the second one is an incident."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        session = self.Session()
        try:
            rows = self._rows_since(session, cutoff)
        finally:
            session.close()

        by_reason: Dict[str, int] = {}
        by_class: Dict[str, int] = {c: 0 for c in ALL_CLASSES}
        by_surface: Dict[str, Dict[str, int]] = {}
        daily: Dict[str, Dict[str, int]] = {}

        responses = len(rows)
        responses_with_rejection = 0
        responses_fully_rejected = 0
        items_total = 0
        items_rejected = 0

        for row in rows:
            counts = json.loads(row.reason_counts) if row.reason_counts else {}
            items_total += row.items_total
            items_rejected += row.items_rejected
            if row.items_rejected > 0 or row.response_rejected:
                responses_with_rejection += 1
            if row.response_rejected:
                responses_fully_rejected += 1

            surface_bucket = by_surface.setdefault(
                row.surface, {"responses": 0, "responses_with_rejection": 0, "items_total": 0, "items_rejected": 0}
            )
            surface_bucket["responses"] += 1
            surface_bucket["items_total"] += row.items_total
            surface_bucket["items_rejected"] += row.items_rejected
            if row.items_rejected > 0 or row.response_rejected:
                surface_bucket["responses_with_rejection"] += 1

            day = row.occurred_at[:10]
            day_bucket = daily.setdefault(day, {"responses": 0, "responses_with_rejection": 0, "rejections": 0, **{c: 0 for c in ALL_CLASSES}})
            day_bucket["responses"] += 1
            if row.items_rejected > 0 or row.response_rejected:
                day_bucket["responses_with_rejection"] += 1

            for reason, count in counts.items():
                by_reason[reason] = by_reason.get(reason, 0) + count
                cls = REASON_CLASS.get(reason, MALFORMED_OUTPUT)
                by_class[cls] += count
                day_bucket["rejections"] += count
                day_bucket[cls] = day_bucket.get(cls, 0) + count

        return {
            "lookback_hours": lookback_hours,
            "responses_validated": responses,
            "responses_with_rejection": responses_with_rejection,
            "responses_fully_rejected": responses_fully_rejected,
            "response_rejection_rate": _rate(responses_with_rejection, responses),
            "items_total": items_total,
            "items_rejected": items_rejected,
            "item_rejection_rate": _rate(items_rejected, items_total),
            "hallucination_rejections": by_class[HALLUCINATION],
            "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
            "by_class": by_class,
            "by_surface": by_surface,
            "daily": [{"day": d, **v} for d, v in sorted(daily.items())],
        }

    def list_recent(self, limit: int = 50, rejections_only: bool = True) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            query = session.query(LLMValidationEventModel)
            if rejections_only:
                query = query.filter(
                    (LLMValidationEventModel.items_rejected > 0) | (LLMValidationEventModel.response_rejected == 1)
                )
            rows = query.order_by(LLMValidationEventModel.occurred_at.desc()).limit(limit).all()
            return [
                {
                    "event_id": r.event_id,
                    "occurred_at": r.occurred_at,
                    "surface": r.surface,
                    "model_name": r.model_name,
                    "items_total": r.items_total,
                    "items_accepted": r.items_accepted,
                    "items_rejected": r.items_rejected,
                    "response_rejected": bool(r.response_rejected),
                    "reason_counts": json.loads(r.reason_counts) if r.reason_counts else {},
                    "reason_classes": sorted(
                        {REASON_CLASS.get(reason, MALFORMED_OUTPUT) for reason in (json.loads(r.reason_counts) or {})}
                    ),
                    "sample": r.sample_redacted,
                }
                for r in rows
            ]
        finally:
            session.close()

    def prune(self, older_than_days: int = 90) -> int:
        """Drops rows past the retention horizon. Returns rows deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        session = self.Session()
        try:
            deleted = (
                session.query(LLMValidationEventModel)
                .filter(LLMValidationEventModel.occurred_at < cutoff)
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(deleted)
        finally:
            session.close()


def _rate(numerator: int, denominator: int) -> float:
    """Rate as a 0-1 fraction, rounded. Returns 0.0 for an empty
    denominator rather than raising or returning None -- callers chart
    this, and "no data yet" and "no rejections yet" both mean "nothing to
    alarm about" here."""
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
