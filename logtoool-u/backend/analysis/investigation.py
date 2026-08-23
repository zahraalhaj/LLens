"""
Deterministic investigation-case assembly -- Phase 7 of the LLens
Multi-Log Analysis Strategy.

Builds everything in InvestigationCase EXCEPT the narrative statements
(case_summary.narrative) directly from Phase 2-6 output: NormalizedEvent,
CorrelatedFlow, FlowLifecycle, DependencyMetrics, Finding. No LLM
involved anywhere in this module -- the LLM (backend/llm/investigate.py)
only fills in the narrative afterward, and even then under validation.

SENSITIVE DATA: this module builds the exact payload that gets shown to
an LLM (see backend/llm/investigate.py's build_prompt_payload()). It
deliberately reads only already-masked NormalizedEvent fields
(masked_mobile, masked_email, card_last4, otp_reference_code) and NEVER
raw_reference or any other raw log text -- see build_llm_safe_timeline().
This is also how "the AI must not analyze raw logs directly" is enforced:
structurally, not just by prompt instruction.
"""
from collections import defaultdict
from typing import Dict, List, Optional

from backend.analysis.correlation_schema import CorrelatedFlow, CorrelationConflict
from backend.analysis.dependency_schema import DependencyMetrics
from backend.analysis.failure_schema import Finding
from backend.analysis.investigation_schema import (
    CaseSummary,
    ChartMetric,
    CorrelationKeyView,
    CorrelationSummary,
    DataQualitySummary,
    FindingSummary,
    InvestigationCase,
    RoutingRecommendation,
)
from backend.analysis.lifecycle_schema import FlowLifecycle, TimelineEntry
from backend.analysis.normalized_schema import NormalizedEvent


def build_llm_safe_timeline(timeline: List[TimelineEntry]) -> List[dict]:
    """The exact event data an LLM is allowed to see: structural fields
    only (stage, event_type, timestamp, level, family, file) -- never
    raw_reference, which can carry unmasked PII/PAN/OTP content (see
    Phase 2's documented limitation that source parsers sometimes embed
    raw sensitive values in `raw`). Takes a plain timeline list (not a
    whole FlowLifecycle/InvestigationCase) so it's reusable from both."""
    return [
        {
            "event_id": entry.source_event_id,
            "log_family": entry.log_family,
            "source_file": entry.source_file,
            "event_timestamp": entry.event_timestamp,
            "event_type": entry.event_type,
            "stage": entry.stage,
            "level": entry.level,
        }
        for entry in timeline
    ]


def _correlation_summary(flow: CorrelatedFlow, conflicts: Optional[List[CorrelationConflict]]) -> CorrelationSummary:
    flow_conflicts = [c for c in (conflicts or []) if flow.flow_id in c.affected_flow_ids]
    is_conflict = flow.correlation_status.value == "CONFLICT" or bool(flow_conflicts)

    conflict_statement = None
    if is_conflict:
        if flow_conflicts:
            details = "; ".join(
                f"{ci.key_type} disagrees ({ci.flow_a_value!r} vs {ci.flow_b_value!r})"
                for c in flow_conflicts
                for ci in c.conflicting_identifiers
            )
            conflict_statement = f"CORRELATION CONFLICT: {details or 'conflicting identifiers prevented an automatic merge.'}"
        else:
            conflict_statement = (
                "CORRELATION CONFLICT: this flow's correlation was blocked by a disagreeing high-confidence "
                "identifier; see backend/analysis/correlate.py's conflict detection for the specific keys involved."
            )

    return CorrelationSummary(
        correlation_status=flow.correlation_status.value,
        correlation_confidence=flow.correlation_confidence.value if flow.correlation_confidence else None,
        correlation_keys=[
            CorrelationKeyView(key_type=k.key_type, value=k.value, confidence=k.confidence.value)
            for k in flow.correlation_keys
        ],
        is_conflict=is_conflict,
        conflict_statement=conflict_statement,
    )


def _chart_metrics(lifecycle: FlowLifecycle, dependency_metrics: Optional[Dict[str, DependencyMetrics]]) -> List[ChartMetric]:
    metrics: List[ChartMetric] = []
    for sd in lifecycle.stage_durations:
        if sd.duration_seconds is not None:
            metrics.append(ChartMetric(label=f"{sd.from_stage} -> {sd.to_stage}", value=sd.duration_seconds, unit="seconds"))
    for name, dep in (dependency_metrics or {}).items():
        if dep.median_latency_ms is not None:
            metrics.append(ChartMetric(label=f"{name} median latency", value=dep.median_latency_ms, unit="ms"))
        if dep.p95_latency_ms is not None:
            metrics.append(ChartMetric(label=f"{name} p95 latency", value=dep.p95_latency_ms, unit="ms"))
    return metrics


def _data_quality(flow: CorrelatedFlow, lifecycle: FlowLifecycle, events: List[NormalizedEvent]) -> DataQualitySummary:
    member_events = [e for e in events if e.source_event_id in set(flow.linked_event_ids)]
    parse_statuses = {e.parse_status for e in member_events}
    parse_status = "failed" if "failed" in parse_statuses else ("partial" if "partial" in parse_statuses else "parsed")
    evidence_levels = [e.evidence_level for e in member_events]
    evidence_level = min(evidence_levels, key=lambda lvl: {"full": 0, "partial": 1, "minimal": 2}.get(lvl, 3)) if evidence_levels else None
    # "full" only if EVERY member event is full -- one degraded event degrades the flow's overall evidence level.
    if evidence_levels and any(lvl != "full" for lvl in evidence_levels):
        evidence_level = "partial" if all(lvl in ("full", "partial") for lvl in evidence_levels) else "minimal"

    return DataQualitySummary(
        evidence_level=evidence_level,
        parse_status=parse_status,
        missing_stages=list(lifecycle.missing_stages),
        not_observable_stages=list(lifecycle.not_observable_stages),
        duplicate_event_ids=list(flow.duplicate_event_ids),
    )


def build_investigation_case(
    flow: CorrelatedFlow,
    events: List[NormalizedEvent],
    lifecycle: FlowLifecycle,
    findings: Optional[List[Finding]] = None,
    conflicts: Optional[List[CorrelationConflict]] = None,
    dependency_metrics: Optional[Dict[str, DependencyMetrics]] = None,
) -> InvestigationCase:
    """Assembles every field of InvestigationCase EXCEPT
    case_summary.narrative, which backend/llm/investigate.py fills in
    afterward. Safe to call with no LLM at all -- the result is a complete,
    valid, useful case with an empty narrative list."""
    findings = findings or []
    flow_findings = [f for f in findings if flow.flow_id in f.affected_flow_ids or set(f.evidence_event_ids) & set(flow.linked_event_ids)]
    finding_summaries = [
        FindingSummary(
            finding_type=f.finding_type.value,
            severity=f.severity.value,
            statement=f.statement,
            confidence=f.confidence.value,
            evidence_event_ids=f.evidence_event_ids,
        )
        for f in flow_findings
    ]

    routing_by_team: Dict[str, List[Finding]] = defaultdict(list)
    for f in flow_findings:
        routing_by_team[f.suggested_route.value].append(f)
    routing = [
        RoutingRecommendation(
            suggested_team=team,
            reason=f"{len(team_findings)} finding(s) of type: {', '.join(sorted({tf.finding_type.value for tf in team_findings}))}.",
            confidence=(
                "HIGH" if any(tf.confidence.value == "HIGH" for tf in team_findings)
                else "MEDIUM" if any(tf.confidence.value == "MEDIUM" for tf in team_findings)
                else "LOW"
            ),
        )
        for team, team_findings in routing_by_team.items()
    ]

    case_summary = CaseSummary(
        transaction_id=flow.transaction_id,
        merchant_name=flow.merchant_name,
        amount=flow.amount,
        currency=flow.currency,
        issuer_id=flow.issuer_id,
        authentication_method=flow.authentication_method,
        final_status=lifecycle.terminal_status.value,
        last_successful_stage=lifecycle.last_confirmed_stage,
        missing_next_stage=lifecycle.missing_next_stage,
        narrative=[],
    )

    return InvestigationCase(
        flow_id=flow.flow_id,
        case_summary=case_summary,
        timeline=list(lifecycle.timeline),
        findings=finding_summaries,
        correlation=_correlation_summary(flow, conflicts),
        routing=routing,
        chart_metrics=_chart_metrics(lifecycle, dependency_metrics),
        data_quality=_data_quality(flow, lifecycle, events),
        ai_available=False,
        ai_status_message=None,
    )


def find_similar_incidents(
    target_flow_id: str,
    target_findings: List[Finding],
    other_flow_findings: Dict[str, List[Finding]],
) -> List[str]:
    """Deterministic lookup: OTHER flow_ids (never the target itself)
    whose findings share at least one finding_type with the target's.
    Returns flow_ids only -- never fabricated, never containing raw event
    content. Used to ground the LLM's "similar incidents" narrative so it
    can't invent incidents that were never actually found."""
    target_types = {f.finding_type for f in target_findings}
    if not target_types:
        return []
    similar = [
        other_flow_id
        for other_flow_id, other_findings in other_flow_findings.items()
        if other_flow_id != target_flow_id and target_types & {f.finding_type for f in other_findings}
    ]
    return sorted(similar)
