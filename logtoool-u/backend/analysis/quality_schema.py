"""
Data-Quality & Analytical-Confidence Monitoring Model -- Phase 9 of the
LLens Multi-Log Analysis Strategy.

This is LLens auditing ITSELF: does the pipeline (Phases 2-3) actually have
enough reliable evidence to support the conclusions built on top of it
(Phases 4-8)? Nothing here is a judgment about the underlying payment
transactions -- it's a judgment about the DATA.

SENSITIVE DATA: SensitiveDataFinding NEVER stores the raw matched value --
only a category, a non-reversible hash reference, and (only where a safe
partial representation already exists via Phase 2's own masking
utilities, e.g. a PAN's last 4 digits) a `safe_hint`. See
backend/analysis/quality.py's scan_for_sensitive_data().
"""
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from backend.analysis.correlation_schema import CorrelationStatus
from backend.analysis.normalized_schema import OptStr


class FieldCheckStatus(str, Enum):
    CONSISTENT = "CONSISTENT"
    MISMATCH = "MISMATCH"
    MISSING_VALUE = "MISSING_VALUE"  # zero non-null occurrences of this field across the flow -- nothing to compare


class SensitiveDataCategory(str, Enum):
    PAN = "PAN"
    OTP = "OTP"
    FULL_MOBILE = "FULL_MOBILE"
    FULL_EMAIL = "FULL_EMAIL"
    SECRET = "SECRET"


class ParseQualityCounts(BaseModel):
    parse_success: int = 0
    parse_failure: int = 0
    partial_parsing: int = 0
    fallback_parsing: int = 0  # succeeded, but via a secondary/regex recovery path -- see quality.py


class EvidenceQualityCounts(BaseModel):
    missing_identifiers: int = 0  # events with no tracker_no/transaction_id/correlation_id at all
    missing_timestamps: int = 0
    unknown_merchant: int = 0
    unmatched_events: int = 0  # has an identifier, but never matched to another event
    uncorrelated_events: int = 0  # belongs to a flow classified UNCORRELATED


class CorrelationQualityCounts(BaseModel):
    correlation_conflicts: int = 0  # Phase 3 CorrelationConflict count
    low_confidence_correlations: int = 0  # Phase 3 CandidateLink + LowConfidenceHint count


class SensitiveDataFinding(BaseModel):
    category: SensitiveDataCategory
    event_id: OptStr = None
    source_file: OptStr = None
    protected_reference: str  # non-reversible hash -- never the raw value
    safe_hint: OptStr = None  # e.g. "ends in 1234" for a PAN -- only ever a safe, already-established masking format


class FieldConsistencyResult(BaseModel):
    flow_id: str
    field_name: str
    status: FieldCheckStatus
    distinct_values: List[str] = Field(default_factory=list)  # only for MISMATCH -- the normalized values that disagreed
    event_ids_by_value: Dict[str, List[str]] = Field(default_factory=dict)


class ExceptionEntry(BaseModel):
    """One actionable row for the exception table -- always carries enough
    reference (flow_id/event_id/source_file) to follow up, never a raw
    sensitive value."""

    category: str  # e.g. "parse_failure", "field_mismatch", "sensitive_data", "correlation_conflict"
    description: str
    flow_id: OptStr = None
    event_id: OptStr = None
    source_file: OptStr = None


class CorrelationQualityBreakdown(BaseModel):
    by_log_family: Dict[str, Dict[str, int]] = Field(default_factory=dict)  # log_family -> {status: count}
    by_source_file: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    by_confidence: Dict[str, int] = Field(default_factory=dict)  # HIGH/MEDIUM/LOW/"NONE" -> flow count
    parser_note: str = "Each log family maps 1:1 to one parser in this system -- 'by parser' is identical to 'by log family'."


class DataQualityScorecard(BaseModel):
    total_events_analyzed: int = 0
    total_flows_analyzed: int = 0

    parse_quality: ParseQualityCounts = Field(default_factory=ParseQualityCounts)
    evidence_quality: EvidenceQualityCounts = Field(default_factory=EvidenceQualityCounts)
    correlation_quality: CorrelationQualityCounts = Field(default_factory=CorrelationQualityCounts)
    flow_classification_counts: Dict[str, int] = Field(default_factory=dict)  # COMPLETE/PARTIAL/UNCORRELATED/CONFLICT -> count
    field_mismatch_count: int = 0
    sensitive_data_finding_count: int = 0

    overall_score: float = 0.0  # 0-100, deterministic composite -- see quality.py's compute_overall_score()
    score_breakdown: Dict[str, float] = Field(default_factory=dict)  # component name -> contribution, for transparency


class QualityAnalysisResult(BaseModel):
    scorecard: DataQualityScorecard
    correlation_quality_breakdown: CorrelationQualityBreakdown = Field(default_factory=CorrelationQualityBreakdown)
    field_consistency: List[FieldConsistencyResult] = Field(default_factory=list)
    sensitive_data_findings: List[SensitiveDataFinding] = Field(default_factory=list)
    exception_table: List[ExceptionEntry] = Field(default_factory=list)
