"""
Recurring Incident & Pattern Analysis -- Phase 8 of the LLens Multi-Log
Analysis Strategy.

Groups Phase 6 Findings by failure_signature ACROSS all correlated flows
(Phase 3) in the analyzed population, to answer: has this failure happened
before, where is it concentrated, and how does it rank against other
recurring problems. Every count here is a FLOW count (a flow contributing
N raw error lines to one finding still counts once here, per Phase 6's own
aggregation) -- never a raw error-line count.

No LLM anywhere in this module. If an AI explanation is layered on top
(Phase 7's InvestigationAssistant, or a future pattern-level equivalent),
it must be handed these RecurringPattern/PatternAnalysisResult objects as
its only source of truth -- it must never be asked to infer a root cause
that isn't already evidenced by a concrete pattern here.

"HOST" GROUPING: no literal hostname field exists anywhere in the data
model (same gap already documented in Phase 5's dependency analysis) --
`log_family` is used as the closest available proxy, since it identifies
which system/log source produced the evidence.

DEPENDENCY GROUPING: reuses Phase 5's own DEPENDENCY_CONFIG event-type
sets directly (not a new, separate mapping) to determine which
dependency(ies) a flow's evidence touches -- the same definition of
"V+"/"Bank API"/"OOB API"/etc. as backend/analysis/dependency.py, so a
"which dependency is involved" answer here means the same thing it means
there.
"""
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set

from backend.analysis.correlation_schema import CorrelatedFlow
from backend.analysis.dependency import DEPENDENCY_CONFIG
from backend.analysis.failure_schema import Finding
from backend.analysis.lifecycle_schema import FlowLifecycle
from backend.analysis.normalized_schema import NormalizedEvent
from backend.analysis.pattern_schema import (
    ConcentrationInfo,
    EvidenceSample,
    GroupingDimension,
    IncidentAssessment,
    PatternAnalysisResult,
    RecurringPattern,
)

# A pattern resting on fewer than this many affected flows gets an
# explicit low_sample_warning -- its failure_rate is still reported (never
# hidden), but flagged as statistically unreliable rather than silently
# presented as equally trustworthy as a high-volume pattern. This is the
# concrete mechanism behind "do not rank a tiny sample as a major issue
# simply because its percentage is high" -- see rank_patterns()'s sort key,
# which ranks on absolute affected_flows count BEFORE failure_rate.
MIN_RELIABLE_SAMPLE_SIZE = 5

# A sub-dimension (issuer/host/queue/etc.) is reported as "concentrated"
# when one value accounts for at least this fraction of a pattern's
# affected flows.
CONCENTRATION_THRESHOLD = 0.6

_MAX_REPRESENTATIVE_EVIDENCE = 5
_MAX_EVENT_IDS_PER_EVIDENCE_SAMPLE = 5


def _dependencies_touched_by_events(events: List[NormalizedEvent]) -> Set[str]:
    """Reuses Phase 5's own DEPENDENCY_CONFIG (family + event_type sets)
    to decide which dependency(ies) a set of events touches -- the exact
    same definition compute_dependency_metrics() uses, not a new mapping."""
    touched: Set[str] = set()
    for event in events:
        for dep, config in DEPENDENCY_CONFIG.items():
            all_types = config.request_types | config.success_types | config.error_types | config.timeout_types
            if event.log_family.value in config.families and (event.event_type in all_types):
                touched.add(dep.value)
    return touched


def _time_window_key(ts: Optional[str], bucket_minutes: int) -> Optional[str]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if bucket_minutes >= 1440:
        bucket_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif bucket_minutes >= 60:
        bucket_dt = dt.replace(minute=0, second=0, microsecond=0)
    else:
        floored_minute = (dt.minute // bucket_minutes) * bucket_minutes
        bucket_dt = dt.replace(minute=floored_minute, second=0, microsecond=0)
    return bucket_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _concentration(dimension: GroupingDimension, counter: Counter, sample_count: int) -> ConcentrationInfo:
    if not counter or sample_count == 0:
        return ConcentrationInfo(dimension=dimension, sample_count=sample_count)
    dominant_value, dominant_count = counter.most_common(1)[0]
    ratio = round(dominant_count / sample_count, 4)
    return ConcentrationInfo(
        dimension=dimension,
        dominant_value=dominant_value,
        ratio=ratio,
        sample_count=sample_count,
        is_concentrated=ratio >= CONCENTRATION_THRESHOLD,
    )


def analyze_recurring_patterns(
    flows: List[CorrelatedFlow],
    events: List[NormalizedEvent],
    findings: List[Finding],
    lifecycles: Optional[List[FlowLifecycle]] = None,
    time_bucket_minutes: int = 1440,
) -> PatternAnalysisResult:
    """Primary entry point. `flows` defines the analyzed population (the
    denominator for every failure_rate); `findings` should already be
    flow-aggregated (Phase 6's analyze_failures() output) -- this function
    does not re-aggregate raw events itself. `lifecycles` (Phase 4's
    reconstruct_lifecycles() output) supplies the actual lifecycle_stage
    grouping key (last_confirmed_stage); when omitted, stage grouping
    reports "UNKNOWN" rather than substituting an unrelated field."""
    total_flows = len(flows)
    flow_by_id: Dict[str, CorrelatedFlow] = {f.flow_id: f for f in flows}
    events_by_id: Dict[str, NormalizedEvent] = {e.source_event_id: e for e in events if e.source_event_id}
    lifecycle_by_flow_id: Dict[str, FlowLifecycle] = {lc.flow_id: lc for lc in (lifecycles or [])}

    findings_by_signature: Dict[str, List[Finding]] = defaultdict(list)
    for finding in findings:
        findings_by_signature[finding.finding_type.value].append(finding)

    global_by_issuer: Counter = Counter()
    global_by_merchant: Counter = Counter()
    global_by_dependency: Counter = Counter()
    global_by_stage: Counter = Counter()
    global_by_host: Counter = Counter()
    global_by_queue: Counter = Counter()
    global_by_auth: Counter = Counter()
    global_by_time: Counter = Counter()

    patterns: List[RecurringPattern] = []

    for signature, sig_findings in findings_by_signature.items():
        affected_flow_ids: Set[str] = set()
        for f in sig_findings:
            affected_flow_ids.update(fid for fid in f.affected_flow_ids if fid in flow_by_id)
        if not affected_flow_ids:
            continue
        affected_flows_list = [flow_by_id[fid] for fid in affected_flow_ids]

        issuer_counter: Counter = Counter()
        merchant_counter: Counter = Counter()
        dependency_counter: Counter = Counter()
        stage_counter: Counter = Counter()
        host_counter: Counter = Counter()
        queue_counter: Counter = Counter()
        auth_counter: Counter = Counter()
        time_counter: Counter = Counter()
        first_seen_candidates: List[str] = []
        last_seen_candidates: List[str] = []

        for flow in affected_flows_list:
            if flow.issuer_id:
                issuer_counter[flow.issuer_id] += 1
                global_by_issuer[flow.issuer_id] += 1
            if flow.merchant_name:
                merchant_counter[flow.merchant_name] += 1
                global_by_merchant[flow.merchant_name] += 1
            if flow.authentication_method:
                auth_counter[flow.authentication_method] += 1
                global_by_auth[flow.authentication_method] += 1
            for family in flow.log_families:
                host_counter[family] += 1
                global_by_host[family] += 1

            flow_events = [events_by_id[eid] for eid in flow.linked_event_ids if eid in events_by_id]
            for dep in _dependencies_touched_by_events(flow_events):
                dependency_counter[dep] += 1
                global_by_dependency[dep] += 1
            for event in flow_events:
                if event.queue_name:
                    queue_counter[event.queue_name] += 1
                    global_by_queue[event.queue_name] += 1

            if flow.first_timestamp:
                first_seen_candidates.append(flow.first_timestamp)
            if flow.last_timestamp:
                last_seen_candidates.append(flow.last_timestamp)
            window_key = _time_window_key(flow.first_timestamp, time_bucket_minutes)
            if window_key:
                time_counter[window_key] += 1
                global_by_time[window_key] += 1

        for flow in affected_flows_list:
            lifecycle = lifecycle_by_flow_id.get(flow.flow_id)
            stage_value = lifecycle.last_confirmed_stage if lifecycle and lifecycle.last_confirmed_stage else "UNKNOWN"
            stage_counter[stage_value] += 1
            global_by_stage[stage_value] += 1

        affected_count = len(affected_flow_ids)
        failure_rate = round(affected_count / total_flows, 4) if total_flows else 0.0

        representative_evidence = [
            EvidenceSample(
                flow_id=f.affected_flow_ids[0] if f.affected_flow_ids else "unknown",
                event_ids=f.evidence_event_ids[:_MAX_EVENT_IDS_PER_EVIDENCE_SAMPLE],
                source_files=f.source_files,
            )
            for f in sig_findings[:_MAX_REPRESENTATIVE_EVIDENCE]
        ]

        low_sample_warning = None
        if affected_count < MIN_RELIABLE_SAMPLE_SIZE:
            low_sample_warning = (
                f"Based on only {affected_count} affected flow(s) out of {total_flows} analyzed -- "
                f"sample size below the {MIN_RELIABLE_SAMPLE_SIZE}-flow reliability threshold. "
                "Treat this failure_rate as indicative, not conclusive."
            )

        patterns.append(
            RecurringPattern(
                pattern=signature,
                grouping_dimension=GroupingDimension.FAILURE_SIGNATURE,
                affected_flows=affected_count,
                total_flows=total_flows,
                failure_rate=failure_rate,
                first_seen=min(first_seen_candidates) if first_seen_candidates else None,
                last_seen=max(last_seen_candidates) if last_seen_candidates else None,
                affected_issuers=sorted(issuer_counter.keys()),
                affected_merchants=sorted(merchant_counter.keys()),
                affected_dependencies=sorted(dependency_counter.keys()),
                representative_evidence=representative_evidence,
                concentration=[
                    _concentration(GroupingDimension.ISSUER, issuer_counter, affected_count),
                    _concentration(GroupingDimension.DEPENDENCY, dependency_counter, affected_count),
                    _concentration(GroupingDimension.LIFECYCLE_STAGE, stage_counter, affected_count),
                    _concentration(GroupingDimension.HOST, host_counter, affected_count),
                    _concentration(GroupingDimension.QUEUE, queue_counter, affected_count),
                    _concentration(GroupingDimension.AUTHENTICATION_METHOD, auth_counter, affected_count),
                    _concentration(GroupingDimension.TIME_WINDOW, time_counter, affected_count),
                ],
                low_sample_warning=low_sample_warning,
            )
        )

    ranked = rank_patterns(patterns)

    return PatternAnalysisResult(
        total_flows_analyzed=total_flows,
        patterns=ranked,
        by_issuer=dict(global_by_issuer),
        by_merchant=dict(global_by_merchant),
        by_dependency=dict(global_by_dependency),
        by_lifecycle_stage=dict(global_by_stage),
        by_host=dict(global_by_host),
        by_queue=dict(global_by_queue),
        by_authentication_method=dict(global_by_auth),
        by_time_window=dict(global_by_time),
    )


def rank_patterns(patterns: List[RecurringPattern]) -> List[RecurringPattern]:
    """Ranking priority, exactly as specified: (1) affected flow count,
    (2) failure rate, (3) recency, (4) dependency concentration. Sorting
    on affected_flows FIRST (before failure_rate) is what prevents a
    tiny-sample pattern (e.g. 1/2 flows = 50%) from outranking a
    high-volume one (e.g. 20/500 = 4%) -- see MIN_RELIABLE_SAMPLE_SIZE."""

    def dependency_concentration_ratio(p: RecurringPattern) -> float:
        for c in p.concentration:
            if c.dimension == GroupingDimension.DEPENDENCY:
                return c.ratio or 0.0
        return 0.0

    ordered = sorted(
        patterns,
        key=lambda p: (p.affected_flows, p.failure_rate, p.last_seen or "", dependency_concentration_ratio(p)),
        reverse=True,
    )
    for i, pattern in enumerate(ordered, start=1):
        pattern.rank = i
    return ordered


def assess_incident(flow_id: str, result: PatternAnalysisResult, flow_findings: List[Finding]) -> IncidentAssessment:
    """Direct answer to "is this transaction an isolated incident? / has
    the same failure happened before?" for one target flow. `flow_findings`
    is the target flow's own subset of Phase 6 findings (e.g. filtered by
    `flow_id in f.affected_flow_ids`) -- RecurringPattern intentionally
    doesn't retain full per-flow membership (only counts + samples, to
    keep the report compact), so matching happens by signature name here.
    Isolated means every one of this flow's failure signatures has exactly
    1 affected flow (this one) in the whole analyzed population."""
    signatures = {f.finding_type.value for f in flow_findings}
    matching = [p for p in result.patterns if p.pattern in signatures]

    if not matching:
        return IncidentAssessment(
            flow_id=flow_id,
            is_isolated=True,
            statement=f"No known failure signature associated with this flow was found in the analyzed population of {result.total_flows_analyzed} flow(s).",
            matching_pattern_names=[],
        )

    recurring = [p for p in matching if p.affected_flows > 1]
    if not recurring:
        pattern_names = ", ".join(p.pattern for p in matching)
        return IncidentAssessment(
            flow_id=flow_id,
            is_isolated=True,
            statement=(
                f"This flow's failure signature(s) ({pattern_names}) appeared in only 1 of "
                f"{result.total_flows_analyzed} analyzed flow(s) -- isolated incident, not observed elsewhere."
            ),
            matching_pattern_names=[p.pattern for p in matching],
        )

    details = "; ".join(f"{p.pattern}: {p.affected_flows}/{p.total_flows} flows ({p.failure_rate:.1%})" for p in recurring)
    return IncidentAssessment(
        flow_id=flow_id,
        is_isolated=False,
        statement=f"This failure has happened before -- {details}.",
        matching_pattern_names=[p.pattern for p in matching],
    )
