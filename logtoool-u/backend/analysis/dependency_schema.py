"""
Operational & Dependency Analysis Model -- Phase 5 of the LLens Multi-Log
Analysis Strategy.

Defines the output shapes for backend/analysis/dependency.py: deterministic
request/response pairing and latency metrics per dependency (V+,
Postilion, Database/SQL, Bank API, OOB API, OTP Online Processor), plus the
OTP application->processor->downstream-provider queue handoff chain.
"""
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from backend.analysis.normalized_schema import OptFloat, OptStr


class Dependency(str, Enum):
    V_PLUS = "V_PLUS"
    POSTILION = "POSTILION"
    DATABASE_SQL = "DATABASE_SQL"
    BANK_API = "BANK_API"
    OOB_API = "OOB_API"
    OTP_ONLINE_PROCESSOR = "OTP_ONLINE_PROCESSOR"


class PairOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    INCOMPLETE = "INCOMPLETE"  # no response found -- and no explicit error/timeout evidence either


class RequestResponsePair(BaseModel):
    request_event_id: OptStr = None
    response_event_id: OptStr = None
    join_key: OptStr = None  # the tracker_no/correlation_id value events were paired on
    log_family: OptStr = None
    request_timestamp: OptStr = None
    response_timestamp: OptStr = None
    latency_ms: OptFloat = None  # None whenever a response is missing -- NEVER fabricated
    outcome: PairOutcome
    event_type: OptStr = None  # the response/error event's own type, when one exists
    failure_signature: OptStr = None
    is_duplicate_response: bool = False  # an extra response for a join_key whose request was already paired
    issuer_id: OptStr = None  # from whichever of request/response carries it -- lets latency be grouped "by issuer"
    queue_name: OptStr = None  # same idea, for "which queue has the highest latency"


class DependencyMetrics(BaseModel):
    dependency: Dependency
    request_count: int = 0
    completed_responses: int = 0  # requests resolved with EITHER a success or an explicit error/timeout
    missing_responses: int = 0  # requests with no resolution at all
    successful_responses: int = 0
    errors: int = 0  # includes timeouts (timeout_count is the specific subset)
    incomplete_requests: int = 0  # == missing_responses -- both names kept per the Phase 5 spec's explicit field list
    median_latency_ms: OptFloat = None
    p95_latency_ms: OptFloat = None
    error_rate: OptFloat = None  # errors / completed_responses ; None when completed_responses == 0
    timeout_count: int = 0
    failure_signatures: Dict[str, int] = Field(default_factory=dict)  # signature -> occurrence count
    duplicate_responses: int = 0
    delayed_count: int = 0  # completed pairs whose latency exceeded the configured expected_latency_ms
    expected_latency_ms: Optional[int] = None
    pairs: List[RequestResponsePair] = Field(default_factory=list)
    note: OptStr = None  # set when this dependency has no observable request boundary (e.g. Database/SQL)


class TimeBucketMetrics(BaseModel):
    bucket_start: str  # ISO-8601 UTC, the bucket's opening timestamp
    volume: int = 0  # requests observed in this bucket
    median_latency_ms: OptFloat = None
    p95_latency_ms: OptFloat = None
    error_rate: OptFloat = None
    incomplete_rate: OptFloat = None


class DependencyTimeSeriesReport(BaseModel):
    dependency: Dependency
    bucket_minutes: int
    buckets: List[TimeBucketMetrics] = Field(default_factory=list)


class HandoffStage(str, Enum):
    OTP_GENERATED = "OTP_GENERATED"
    APPLICATION_QUEUE_CONFIRMED = "APPLICATION_QUEUE_CONFIRMED"
    PROCESSOR_RECEIVED = "PROCESSOR_RECEIVED"
    DOWNSTREAM_QUEUE_SELECTED = "DOWNSTREAM_QUEUE_SELECTED"
    VALIDATED = "VALIDATED"


class TrackerHandoffRecord(BaseModel):
    """One IA tracker's progress through the 5-stage OTP handoff chain."""

    tracker_no: str
    stage_event_ids: Dict[str, str] = Field(default_factory=dict)  # stage -> source_event_id, only for reached stages
    stage_timestamps: Dict[str, str] = Field(default_factory=dict)  # stage -> event_timestamp
    reached_stage: OptStr = None  # furthest stage reached, in chain order
    has_application_event: bool = False
    has_processor_event: bool = False


class TransitionLatency(BaseModel):
    from_stage: str
    to_stage: str
    sample_count: int = 0
    median_ms: OptFloat = None
    p95_ms: OptFloat = None


class QueueHandoffReport(BaseModel):
    generated_messages: int = 0
    application_queued_messages: int = 0
    processor_received_messages: int = 0
    downstream_routed_messages: int = 0
    validated_messages: int = 0

    unmatched_messages: int = 0  # application-queued IA trackers with NO processor_received event
    unmatched_tracker_nos: List[str] = Field(default_factory=list)
    orphan_messages: int = 0  # processor_received IA trackers with NO application_queued event
    orphan_tracker_nos: List[str] = Field(default_factory=list)

    queue_distribution: Dict[str, int] = Field(default_factory=dict)  # queue_name -> message count
    transition_latencies: List[TransitionLatency] = Field(default_factory=list)
    tracker_records: List[TrackerHandoffRecord] = Field(default_factory=list)
