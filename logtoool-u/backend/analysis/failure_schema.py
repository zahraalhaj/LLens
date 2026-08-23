"""
Deterministic Failure Analysis Model -- Phase 6 of the LLens Multi-Log
Analysis Strategy.

Defines the output shapes for backend/analysis/failure.py: normalized
failure signatures, routable findings, and aggregate counts. Routing is a
SUGGESTED OWNING TEAM, never proven blame and never an individual person --
see RoutingArea and Finding.suggested_route below.
"""
from enum import Enum
from typing import Dict, List

from pydantic import BaseModel, Field


class FailureSignature(str, Enum):
    V_PLUS_MQ_TIMEOUT = "V_PLUS_MQ_TIMEOUT"
    INVALID_STEPUP_ID = "INVALID_STEPUP_ID"
    OOB_HTTP_ERROR = "OOB_HTTP_ERROR"
    OOB_BUSINESS_ERROR = "OOB_BUSINESS_ERROR"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    HOST_RESPONSE_CODE_FAILURE = "HOST_RESPONSE_CODE_FAILURE"
    QUEUE_GAP = "QUEUE_GAP"
    MISSING_PROCESSOR_RECEIPT = "MISSING_PROCESSOR_RECEIPT"
    CORRELATION_CONFLICT = "CORRELATION_CONFLICT"
    PARSER_FAILURE = "PARSER_FAILURE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Confidence(str, Enum):
    HIGH = "HIGH"  # an exact rule matched, or the finding is derived from clean structured data (Phase 3/5 output)
    MEDIUM = "MEDIUM"  # the text-normalization/tokenization fallback matched a known signature's keyword set
    LOW = "LOW"  # no rule or keyword set matched anything -- UNKNOWN_ERROR


class RoutingArea(str, Enum):
    """A SUGGESTED OWNING TEAM for triage -- never proven blame, and never
    an individual person. See Finding.suggested_route."""

    APPLICATION_SUPPORT = "Application Support"
    MIDDLEWARE_VPLUS = "Middleware / V+"
    ISSUER_HOST = "Issuer Host"
    BANK_API = "Bank API"
    OOB_INTEGRATION = "OOB Integration"
    MESSAGING_QUEUE = "Messaging / Queue"
    PARSER_DATA_QUALITY = "Parser / Data Quality"


class Finding(BaseModel):
    finding_type: FailureSignature
    severity: Severity
    statement: str  # deterministic, templated -- never LLM-generated
    confidence: Confidence
    evidence_event_ids: List[str] = Field(default_factory=list)  # every raw event id aggregated into this finding
    source_files: List[str] = Field(default_factory=list)
    suggested_route: RoutingArea

    # Aggregation support -- "count affected flows, not raw error lines":
    affected_flow_ids: List[str] = Field(default_factory=list)
    occurrence_count: int = 1  # number of raw events collapsed into this finding (transparency, not the headline count)


class FailureAnalysisResult(BaseModel):
    findings: List[Finding] = Field(default_factory=list)
    # finding_type -> number of DISTINCT AFFECTED FLOWS (not raw event count) -- the aggregation this phase requires
    signature_flow_counts: Dict[str, int] = Field(default_factory=dict)
    total_raw_events_analyzed: int = 0
    total_affected_flows: int = 0
