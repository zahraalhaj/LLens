"""
AI Investigation & Explanation Model -- Phase 7 of the LLens Multi-Log
Analysis Strategy.

The deterministic engine (Phases 2-6) remains authoritative: every
structured field on InvestigationCase (case_summary's facts, timeline,
findings, correlation, routing, chart_metrics, data_quality) is populated
directly from Phase 2-6 output in backend/analysis/investigation.py, with
ZERO LLM involvement. The LLM (backend/llm/investigate.py) contributes
ONLY the `narrative` statements inside case_summary -- and even those are
validated against the deterministic facts before being accepted (see
_validate_statements() in investigate.py), never trusted blindly.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from backend.analysis.lifecycle_schema import TimelineEntry
from backend.analysis.normalized_schema import OptFloat, OptStr


class StatementType(str, Enum):
    FACT = "FACT"
    INTERPRETATION = "INTERPRETATION"
    MISSING_EVIDENCE = "MISSING EVIDENCE"


class NarrativeRole(str, Enum):
    """The 9 AI explanation roles from the Phase 7 spec."""

    WHAT_HAPPENED = "what_happened"
    JOURNEY_SUMMARY = "journey_summary"
    STOPPAGE = "where_flow_stopped"
    TECHNICAL_BOUNDARY = "technical_boundary"
    SUPPORTING_EVIDENCE = "supporting_evidence"
    MISSING_EVIDENCE = "missing_evidence"
    NEXT_INVESTIGATION = "next_investigation_area"
    SIMILAR_INCIDENTS = "similar_incidents"
    ANALYST_SUMMARY = "analyst_case_summary"


class NarrativeStatement(BaseModel):
    role: NarrativeRole
    statement_type: StatementType
    text: str
    evidence_event_ids: List[str] = Field(default_factory=list)
    source_files: List[str] = Field(default_factory=list)


class CaseSummary(BaseModel):
    # -- required facts, populated deterministically from Phase 3/4 --
    transaction_id: OptStr = None
    merchant_name: OptStr = None
    amount: OptFloat = None
    currency: OptStr = None
    issuer_id: OptStr = None
    authentication_method: OptStr = None
    final_status: OptStr = None
    last_successful_stage: OptStr = None
    missing_next_stage: OptStr = None

    # -- the AI's contribution: 9 roles, each FACT/INTERPRETATION/MISSING EVIDENCE tagged --
    narrative: List[NarrativeStatement] = Field(default_factory=list)


class FindingSummary(BaseModel):
    finding_type: str
    severity: str
    statement: str
    confidence: str
    evidence_event_ids: List[str] = Field(default_factory=list)


class RoutingRecommendation(BaseModel):
    suggested_team: str  # a RoutingArea value -- a team, never a person (see Phase 6)
    reason: str
    confidence: str


class CorrelationKeyView(BaseModel):
    key_type: str
    value: OptStr = None
    confidence: str


class CorrelationSummary(BaseModel):
    correlation_status: str
    correlation_confidence: OptStr = None
    correlation_keys: List[CorrelationKeyView] = Field(default_factory=list)
    is_conflict: bool = False
    conflict_statement: OptStr = None  # deterministically generated, always starts with "CORRELATION CONFLICT" when is_conflict


class ChartMetric(BaseModel):
    label: str
    value: OptFloat = None
    unit: OptStr = None


class DataQualitySummary(BaseModel):
    evidence_level: OptStr = None
    parse_status: OptStr = None
    missing_stages: List[str] = Field(default_factory=list)
    not_observable_stages: List[str] = Field(default_factory=list)
    duplicate_event_ids: List[str] = Field(default_factory=list)


class InvestigationCase(BaseModel):
    flow_id: str
    case_summary: CaseSummary
    timeline: List[TimelineEntry] = Field(default_factory=list)
    findings: List[FindingSummary] = Field(default_factory=list)
    correlation: CorrelationSummary
    routing: List[RoutingRecommendation] = Field(default_factory=list)
    chart_metrics: List[ChartMetric] = Field(default_factory=list)
    data_quality: DataQualitySummary

    ai_available: bool = False
    ai_status_message: OptStr = None
