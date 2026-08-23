"""
Recurring Incident & Pattern Analysis Model -- Phase 8 of the LLens
Multi-Log Analysis Strategy.

A "pattern" is a recurring FAILURE SIGNATURE observed across multiple
correlated flows (Phase 3) -- not a raw error-line count (Phase 6 already
enforces flow-level aggregation; this phase re-aggregates ACROSS flows to
ask "has this happened before, and where"). Every rate is reported with
its denominator; ranking never lets a tiny sample outrank real volume
(see backend/analysis/pattern.py's ranking key).
"""
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from backend.analysis.normalized_schema import OptFloat, OptStr


class GroupingDimension(str, Enum):
    FAILURE_SIGNATURE = "failure_signature"
    DEPENDENCY = "dependency"
    LIFECYCLE_STAGE = "lifecycle_stage"
    ISSUER = "issuer"
    HOST = "host"  # proxied by log_family -- see pattern.py's module docstring
    QUEUE = "queue"
    AUTHENTICATION_METHOD = "authentication_method"
    TIME_WINDOW = "time_window"


class ConcentrationInfo(BaseModel):
    """Answers "is this pattern concentrated in a specific X" for one
    sub-dimension: the dominant value and what fraction of the pattern's
    affected flows share it."""

    dimension: GroupingDimension
    dominant_value: OptStr = None
    ratio: OptFloat = None  # dominant_value's flow count / affected_flows, in [0,1]
    sample_count: int = 0  # affected_flows this ratio was computed over -- always shown alongside ratio
    is_concentrated: bool = False


class EvidenceSample(BaseModel):
    flow_id: str
    event_ids: List[str] = Field(default_factory=list)
    source_files: List[str] = Field(default_factory=list)


class RecurringPattern(BaseModel):
    pattern: str  # the grouping value, e.g. "V_PLUS_MQ_TIMEOUT"
    grouping_dimension: GroupingDimension = GroupingDimension.FAILURE_SIGNATURE

    affected_flows: int
    total_flows: int  # the denominator -- ALWAYS present alongside failure_rate, never shown without it
    failure_rate: float

    first_seen: OptStr = None
    last_seen: OptStr = None

    affected_issuers: List[str] = Field(default_factory=list)
    affected_merchants: List[str] = Field(default_factory=list)
    affected_dependencies: List[str] = Field(default_factory=list)
    representative_evidence: List[EvidenceSample] = Field(default_factory=list)

    concentration: List[ConcentrationInfo] = Field(default_factory=list)
    low_sample_warning: OptStr = None  # set when affected_flows is below the reliability threshold -- read this before trusting failure_rate
    rank: Optional[int] = None  # 1-based, set after ranking (see pattern.py's rank_patterns())


class IncidentAssessment(BaseModel):
    """Direct answer to "is this transaction an isolated incident? / has
    the same failure happened before?" for one target flow."""

    flow_id: str
    is_isolated: bool
    statement: str  # deterministic, always states the denominator
    matching_pattern_names: List[str] = Field(default_factory=list)


class PatternAnalysisResult(BaseModel):
    total_flows_analyzed: int
    patterns: List[RecurringPattern] = Field(default_factory=list)  # ranked, failure_signature-keyed

    # Global breakdowns across ALL findings combined -- answer "which
    # issuers/merchants/dependencies/etc are affected" without needing to
    # pick one pattern first. Values are AFFECTED-FLOW counts, not raw
    # finding counts (same flow-level aggregation discipline as Phase 6).
    by_issuer: Dict[str, int] = Field(default_factory=dict)
    by_merchant: Dict[str, int] = Field(default_factory=dict)
    by_dependency: Dict[str, int] = Field(default_factory=dict)
    by_lifecycle_stage: Dict[str, int] = Field(default_factory=dict)
    by_host: Dict[str, int] = Field(default_factory=dict)
    by_queue: Dict[str, int] = Field(default_factory=dict)
    by_authentication_method: Dict[str, int] = Field(default_factory=dict)
    by_time_window: Dict[str, int] = Field(default_factory=dict)
