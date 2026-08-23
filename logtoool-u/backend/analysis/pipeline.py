"""
Analytics Pipeline -- Phase 10 of the LLens Multi-Log Analysis Strategy.

The single, authoritative path from stored CanonicalLogEvents to the full
trusted analytical model (Phases 2-9). Every Phase 10 dashboard/API route
builds its response from an AnalysisBundle returned by
run_analysis_pipeline() -- none of them re-reads raw log rows or
recomputes a metric ad hoc. This is what "do not calculate analytics
directly from raw log files" means structurally, not just as a rule.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.analysis.correlate import correlate_events
from backend.analysis.correlation_schema import CorrelatedFlow, CorrelationResult
from backend.analysis.dependency import compute_all_dependencies, compute_otp_handoff_chain
from backend.analysis.dependency_schema import DependencyMetrics, QueueHandoffReport
from backend.analysis.failure import analyze_failures
from backend.analysis.failure_schema import FailureAnalysisResult
from backend.analysis.lifecycle import reconstruct_lifecycles
from backend.analysis.lifecycle_schema import FlowLifecycle
from backend.analysis.normalize import normalize_events
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent
from backend.analysis.pattern import analyze_recurring_patterns
from backend.analysis.pattern_schema import PatternAnalysisResult
from backend.analysis.quality import analyze_data_quality
from backend.analysis.quality_schema import QualityAnalysisResult
from backend.core.store import DatabaseManager

ALL_PAYMENT_FAMILIES: tuple = tuple(f.value for f in LogFamily)

# Single source of truth for "which version of the deterministic engine
# produced this result" -- used by the Phase 11 AI Analyst's audit log
# (backend/llm/audit.py) as the "rule/parser versions" field, since no
# per-parser version numbering scheme exists elsewhere in this codebase.
# Bump this string whenever a change to Phases 2-10 or query_tools.py
# could change an analytical result for the same input data.
ENGINE_VERSION = "llens-analysis-engine-v11"


@dataclass
class AnalysisBundle:
    events: List[NormalizedEvent] = field(default_factory=list)
    correlation_result: Optional[CorrelationResult] = None
    lifecycles: List[FlowLifecycle] = field(default_factory=list)
    failure_result: Optional[FailureAnalysisResult] = None
    dependency_metrics: Dict[str, DependencyMetrics] = field(default_factory=dict)
    queue_handoff: Optional[QueueHandoffReport] = None
    pattern_result: Optional[PatternAnalysisResult] = None
    quality_result: Optional[QualityAnalysisResult] = None

    @property
    def flows(self) -> List[CorrelatedFlow]:
        return self.correlation_result.flows if self.correlation_result else []

    @property
    def lifecycle_by_flow_id(self) -> Dict[str, FlowLifecycle]:
        return {lc.flow_id: lc for lc in self.lifecycles}

    @property
    def event_by_id(self) -> Dict[str, NormalizedEvent]:
        return {e.source_event_id: e for e in self.events if e.source_event_id}


def run_analysis_pipeline(
    db: DatabaseManager,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source_systems: Optional[List[str]] = None,
    limit_per_family: int = 50000,
) -> AnalysisBundle:
    """Fetches raw canonical events per family (bounded, same
    per-source_system query contract every other analysis route already
    uses -- see DatabaseManager.get_events_for_analysis()), normalizes
    (Phase 2), correlates (Phase 3), and runs every downstream
    deterministic phase (4-9). This is the ONLY function a Phase 10 route
    should call for analytical data."""
    families = source_systems or list(ALL_PAYMENT_FAMILIES)
    raw_events: List[dict] = []
    for family in families:
        raw_events.extend(
            db.get_events_for_analysis(source_system=family, date_from=date_from, date_to=date_to, limit=limit_per_family)
        )

    events = normalize_events(raw_events)
    correlation_result = correlate_events(events)
    lifecycles = reconstruct_lifecycles(correlation_result.flows, events)
    queue_handoff = compute_otp_handoff_chain(events)
    failure_result = analyze_failures(
        events, flows=correlation_result.flows, conflicts=correlation_result.conflicts, queue_handoff=queue_handoff
    )
    dependency_metrics = compute_all_dependencies(events)
    pattern_result = analyze_recurring_patterns(
        correlation_result.flows, events, failure_result.findings, lifecycles=lifecycles
    )
    quality_result = analyze_data_quality(events, correlation_result.flows, correlation_result=correlation_result)

    return AnalysisBundle(
        events=events,
        correlation_result=correlation_result,
        lifecycles=lifecycles,
        failure_result=failure_result,
        dependency_metrics=dependency_metrics,
        queue_handoff=queue_handoff,
        pattern_result=pattern_result,
        quality_result=quality_result,
    )
