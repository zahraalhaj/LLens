"""
User-Facing AI Analyst Model -- Phase 11 of the LLens Multi-Log Analysis
Strategy.

The deterministic engine (Phases 2-10, via backend/analysis/query_tools.py)
remains authoritative. The LLM (backend/llm/ai_analyst.py) contributes ONLY
two things, both validated before being trusted: (1) which single
pre-built tool call answers the question -- never a hand-rolled SQL query,
join, or metric -- and (2) the natural-language phrasing of the
ALREADY-COMPUTED tool result. See llm/ai_analyst.py's module docstring for
the full validation contract.
"""
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from backend.analysis.normalized_schema import OptStr


class EvidenceType(str, Enum):
    """The 4-way distinction the AI Analyst must apply to every statement
    -- a more granular split than Phase 7's 3-way FACT/INTERPRETATION/
    MISSING EVIDENCE, since this phase explicitly separates a computed
    number from a directly-observed fact."""

    OBSERVED_FACT = "observed_fact"  # a value taken directly from a normalized event or flow, not computed
    CALCULATED_METRIC = "calculated_metric"  # a count/rate/latency already computed by a Phase 5-9 function
    INFERRED_INTERPRETATION = "inferred_interpretation"  # a reasonable reading of the above, explicitly framed as inference
    MISSING_EVIDENCE = "missing_evidence"  # evidence the question asked about that does not exist in the captured logs


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AnalystStatement(BaseModel):
    evidence_type: EvidenceType
    text: str
    evidence_event_ids: List[str] = Field(default_factory=list)
    flow_ids: List[str] = Field(default_factory=list)


class AnalystAnswer(BaseModel):
    question: str
    answer: str  # part 1: the direct answer, 1-3 sentences
    evidence: List[AnalystStatement] = Field(default_factory=list)  # part 2, each tagged by EvidenceType
    relevant_flow_ids: List[str] = Field(default_factory=list)  # part 3
    metrics: Dict[str, Any] = Field(default_factory=dict)  # part 4: the raw deterministic tool output
    confidence: Confidence = Confidence.LOW  # part 5
    recommended_investigation_area: OptStr = None  # part 6, when appropriate

    tool_used: OptStr = None
    tool_parameters: Dict[str, Any] = Field(default_factory=dict)
    unsupported: bool = False  # true when no deterministic tool could answer the question
    limitation_explanation: OptStr = None  # populated whenever unsupported is true

    ai_available: bool = False
    ai_status_message: OptStr = None
