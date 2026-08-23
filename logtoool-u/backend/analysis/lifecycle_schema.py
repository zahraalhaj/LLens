"""
Transaction Lifecycle Model -- Phase 4 of the LLens Multi-Log Analysis
Strategy.

Defines the output shapes for deterministic lifecycle reconstruction
(backend/analysis/lifecycle.py): given one CorrelatedFlow (Phase 3) and its
member NormalizedEvents (Phase 2), map them onto the 12 canonical
authentication lifecycle stages and report progress, gaps, and outcome --
no LLM, no natural-language explanation, purely rule-based.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.analysis.normalized_schema import OptFloat, OptStr


class LifecycleStage(str, Enum):
    """The 12 canonical stages, in their master chronological order. OTP
    and OOB flows each use a different ordered subsequence of this list
    (see lifecycle.py's OTP_TEMPLATE_STAGES/OOB_TEMPLATE_STAGES) -- they
    share the same relative ordering, diverging only over
    OTP_GENERATED/APPLICATION_QUEUE_CONFIRMED (OTP-only) vs
    PROCESSOR_RECEIVED/DOWNSTREAM_QUEUE_SELECTED/OOB_INITIATED (OOB-only),
    and reconverging at CUSTOMER_RESPONSE_PENDING."""

    REQUEST_RECEIVED = "REQUEST_RECEIVED"
    CARD_LOOKUP_SENT = "CARD_LOOKUP_SENT"
    CARD_LOOKUP_COMPLETED = "CARD_LOOKUP_COMPLETED"
    CHALLENGE_SELECTED = "CHALLENGE_SELECTED"
    OTP_GENERATED = "OTP_GENERATED"
    APPLICATION_QUEUE_CONFIRMED = "APPLICATION_QUEUE_CONFIRMED"
    PROCESSOR_RECEIVED = "PROCESSOR_RECEIVED"
    DOWNSTREAM_QUEUE_SELECTED = "DOWNSTREAM_QUEUE_SELECTED"
    OOB_INITIATED = "OOB_INITIATED"
    CUSTOMER_RESPONSE_PENDING = "CUSTOMER_RESPONSE_PENDING"
    CUSTOMER_RESPONSE_RECEIVED = "CUSTOMER_RESPONSE_RECEIVED"
    AUTH_COMPLETED = "AUTH_COMPLETED"


class AuthTemplate(str, Enum):
    OTP = "OTP"
    OOB = "OOB"


class StageObservationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    MISSING = "MISSING"  # expected, observable, not found in captured logs
    NOT_OBSERVABLE = "NOT_OBSERVABLE"  # expected, but no source log in this flow can ever show it


class TerminalStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"  # no terminal error, but the expected lifecycle didn't complete
    PENDING_AT_LOG_END = "PENDING_AT_LOG_END"  # OOB-specific: still pending as of the last captured event -- NOT a failure
    UNDETERMINED = "UNDETERMINED"  # neither OTP nor OOB template could be inferred for this flow


class TimelineEntry(BaseModel):
    event_no: int
    source_event_id: OptStr = None
    log_family: str
    source_file: str
    event_timestamp: OptStr = None
    event_type: OptStr = None
    stage: OptStr = None  # the canonical LifecycleStage this event maps to, if any
    level: OptStr = None
    raw_reference: str


class StageDuration(BaseModel):
    """Gap between two consecutive CONFIRMED stages (in template order,
    skipping any stage that wasn't observed)."""

    from_stage: str
    to_stage: str
    from_event_id: OptStr = None
    to_event_id: OptStr = None
    duration_seconds: OptFloat = None  # None if either endpoint's timestamp is unparseable


class FailureBoundary(BaseModel):
    """Populated only when terminal_status == FAILED -- where the flow's
    evidence breaks down."""

    after_stage: OptStr = None  # last_confirmed_stage at the moment of failure
    at_event_id: OptStr = None
    log_family: OptStr = None
    event_type: OptStr = None
    level: OptStr = None
    reason: OptStr = None


class FlowLifecycle(BaseModel):
    flow_id: str
    authentication_method: OptStr = None  # passthrough from the CorrelatedFlow
    auth_template: Optional[AuthTemplate] = None  # None when neither OTP nor OOB could be determined

    first_event: Optional[TimelineEntry] = None
    last_event: Optional[TimelineEntry] = None
    timeline: List[TimelineEntry] = Field(default_factory=list)

    last_confirmed_stage: OptStr = None
    expected_next_stage: OptStr = None
    missing_next_stage: OptStr = None  # formatted: "<stage>: not found in captured logs" | "<stage>: NOT_OBSERVABLE" | None

    missing_stages: List[str] = Field(default_factory=list)  # expected + observable + not reached
    not_observable_stages: List[str] = Field(default_factory=list)  # expected but no source log in this flow can show it

    terminal_status: TerminalStatus = TerminalStatus.UNDETERMINED
    failure_boundary: Optional[FailureBoundary] = None

    stage_durations: List[StageDuration] = Field(default_factory=list)
