"""
Dashboard assembly -- Phase 10 of the LLens Multi-Log Analysis Strategy.

Pure functions that build each of the 5 dashboard response shapes (Service
Overview, Dependency Health, Queue & Messaging, Investigation, Security /
Data Quality) from an AnalysisBundle (backend/analysis/pipeline.py). No
raw-log access, no LLM, no new metrics computed here -- everything is a
reshaping/aggregation of Phase 2-9 output for presentation.

Every returned record carries enough IDs (flow_id/event_id/source_file)
for the frontend to drill through to the underlying analytical record --
see each build_* function's "evidence"/"flow_id" fields.
"""
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.analysis.investigation import build_investigation_case, find_similar_incidents
from backend.analysis.lifecycle import MASTER_STAGE_ORDER, OOB_TEMPLATE_STAGES, OTP_TEMPLATE_STAGES
from backend.analysis.lifecycle_schema import AuthTemplate
from backend.analysis.normalized_schema import mask_mobile
from backend.analysis.pipeline import AnalysisBundle

_TEMPLATE_STAGES = {AuthTemplate.OTP: OTP_TEMPLATE_STAGES, AuthTemplate.OOB: OOB_TEMPLATE_STAGES}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _time_bucket(ts: Optional[str], bucket_minutes: int) -> Optional[str]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if bucket_minutes >= 1440:
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif bucket_minutes >= 60:
        dt = dt.replace(minute=0, second=0, microsecond=0)
    else:
        dt = dt.replace(minute=(dt.minute // bucket_minutes) * bucket_minutes, second=0, microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 1. SERVICE OVERVIEW
# ---------------------------------------------------------------------------


def build_service_overview(bundle: AnalysisBundle, time_bucket_minutes: int = 1440) -> Dict[str, Any]:
    flows = bundle.flows
    lifecycle_by_flow = bundle.lifecycle_by_flow_id

    # pending/incomplete/error kept as separate states, never merged -- see module docstring.
    terminal_counts: Counter = Counter(lc.terminal_status.value for lc in bundle.lifecycles)
    success = terminal_counts.get("SUCCESS", 0)
    error = terminal_counts.get("FAILED", 0)
    pending = terminal_counts.get("PENDING_AT_LOG_END", 0)
    incomplete = terminal_counts.get("INCOMPLETE", 0) + terminal_counts.get("UNDETERMINED", 0)

    trend: Dict[str, Counter] = defaultdict(Counter)
    for flow in flows:
        lc = lifecycle_by_flow.get(flow.flow_id)
        bucket = _time_bucket(flow.first_timestamp, time_bucket_minutes)
        if lc and bucket:
            trend[bucket][lc.terminal_status.value] += 1
    outcome_trend = [{"bucket": bucket, **dict(counts)} for bucket, counts in sorted(trend.items())]

    auth_mix: Counter = Counter(lc.auth_template.value for lc in bundle.lifecycles if lc.auth_template)

    top_failure_signatures = [
        {
            "pattern": p.pattern,
            "rank": p.rank,
            "affected_flows": p.affected_flows,
            "total_flows": p.total_flows,
            "failure_rate": p.failure_rate,
            "low_sample_warning": p.low_sample_warning,
            "representative_evidence": [e.model_dump() for e in p.representative_evidence],
        }
        for p in bundle.pattern_result.patterns[:10]
    ] if bundle.pattern_result else []

    return {
        "total_flows": len(flows),
        "success": success,
        "error": error,
        "pending": pending,
        "incomplete": incomplete,
        "outcome_trend": outcome_trend,
        "otp_oob_mix": dict(auth_mix),
        "top_failure_signatures": top_failure_signatures,
        "lifecycle_funnel": _compute_lifecycle_funnel(bundle),
        "missing_stage_counts": _compute_missing_stage_counts(bundle),
        "issuer_failure_rates": _compute_issuer_failure_rates(bundle),
        "failure_heatmap": _compute_failure_heatmap(bundle),
    }


_MAX_SAMPLE_FLOW_IDS = 10


def _compute_lifecycle_funnel(bundle: AnalysisBundle) -> List[Dict[str, Any]]:
    """Cumulative funnel: for each of the 12 canonical stages, how many
    flows reached AT LEAST that stage (per whichever OTP/OOB template that
    flow actually used -- see backend/analysis/lifecycle.py). A stage the
    flow's own template doesn't include (e.g. OTP_GENERATED for an OOB
    flow) simply never counts toward or against it."""
    stage_index = {stage: i for i, stage in enumerate(MASTER_STAGE_ORDER)}
    reached_flow_ids: Dict[str, List[str]] = defaultdict(list)

    for lc in bundle.lifecycles:
        if not lc.auth_template or not lc.last_confirmed_stage:
            continue
        template = _TEMPLATE_STAGES.get(lc.auth_template)
        if not template or lc.last_confirmed_stage not in stage_index:
            continue
        reached_index = stage_index[lc.last_confirmed_stage]
        for stage in template:
            if stage_index[stage] <= reached_index:
                reached_flow_ids[stage].append(lc.flow_id)

    return [
        {
            "stage": stage,
            "flow_count": len(reached_flow_ids.get(stage, [])),
            "sample_flow_ids": reached_flow_ids.get(stage, [])[:_MAX_SAMPLE_FLOW_IDS],
        }
        for stage in MASTER_STAGE_ORDER
    ]


def _compute_missing_stage_counts(bundle: AnalysisBundle) -> List[Dict[str, Any]]:
    """Supports the required missing-stage heatmap: how many flows are
    missing each stage (observable but not found), with drill-through
    flow_ids -- reuses Phase 4's own missing_stages list per flow, never
    recomputed."""
    missing_flow_ids: Dict[str, List[str]] = defaultdict(list)
    for lc in bundle.lifecycles:
        for stage in lc.missing_stages:
            missing_flow_ids[stage].append(lc.flow_id)

    return [
        {"stage": stage, "flow_count": len(flow_ids), "sample_flow_ids": flow_ids[:_MAX_SAMPLE_FLOW_IDS]}
        for stage, flow_ids in sorted(missing_flow_ids.items(), key=lambda kv: -len(kv[1]))
    ]


def _compute_issuer_failure_rates(bundle: AnalysisBundle) -> List[Dict[str, Any]]:
    """Per-issuer total flow count vs. FAILED flow count. Deliberately a
    small standalone copy of backend/analysis/query_tools.py's
    issuer_failure_rates_tool() rather than an import from it -- query_tools
    already imports FROM this module (SEARCHABLE_FIELDS, search_flows,
    build_investigation_result) for its lookup_transaction tool, so this
    module importing back from query_tools would be a circular import.
    Keep both in sync if the aggregation ever changes."""
    lifecycle_by_flow = bundle.lifecycle_by_flow_id
    totals: Dict[str, int] = {}
    failed: Dict[str, int] = {}
    for flow in bundle.flows:
        issuer = flow.issuer_id
        if not issuer:
            continue
        lc = lifecycle_by_flow.get(flow.flow_id)
        totals[issuer] = totals.get(issuer, 0) + 1
        if lc and lc.terminal_status.value == "FAILED":
            failed[issuer] = failed.get(issuer, 0) + 1

    rates = [
        {
            "issuer_id": issuer,
            "total_flows": total,
            "failed_flows": failed.get(issuer, 0),
            "failure_rate": round(failed.get(issuer, 0) / total, 4) if total else None,
        }
        for issuer, total in totals.items()
    ]
    rates.sort(key=lambda r: (-(r["failure_rate"] or 0), -r["total_flows"]))
    return rates


def _compute_failure_heatmap(bundle: AnalysisBundle) -> List[Dict[str, Any]]:
    """Failed-flow density by (day_of_week, hour_of_day), derived from each
    FAILED flow's first_timestamp -- surfaces temporal failure patterns
    (e.g. "V+ timeouts spike every day around 14:00") that a flat Pareto
    chart can't show. Reuses only already-computed lifecycle/flow data, no
    new correlation or classification -- day_of_week is 0=Monday..6=Sunday
    (Python's own datetime.weekday() convention)."""
    flow_by_id = {f.flow_id: f for f in bundle.flows}
    total_by_bucket: Dict[tuple, int] = defaultdict(int)
    failed_by_bucket: Dict[tuple, int] = defaultdict(int)

    for lc in bundle.lifecycles:
        flow = flow_by_id.get(lc.flow_id)
        ts = flow.first_timestamp if flow else None
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        bucket = (dt.weekday(), dt.hour)
        total_by_bucket[bucket] += 1
        if lc.terminal_status.value == "FAILED":
            failed_by_bucket[bucket] += 1

    return [
        {"day_of_week": day, "hour": hour, "failed_count": failed_by_bucket.get((day, hour), 0), "total_count": total}
        for (day, hour), total in sorted(total_by_bucket.items())
    ]


# ---------------------------------------------------------------------------
# 2. DEPENDENCY HEALTH
# ---------------------------------------------------------------------------


def build_dependency_health(bundle: AnalysisBundle) -> Dict[str, Any]:
    dependencies = {}
    for name, metrics in bundle.dependency_metrics.items():
        success_rate = (
            round(metrics.successful_responses / metrics.completed_responses, 4) if metrics.completed_responses else None
        )
        dependencies[name] = {
            "request_volume": metrics.request_count,
            "success_rate": success_rate,
            "error_rate": metrics.error_rate,
            "median_latency_ms": metrics.median_latency_ms,
            "p95_latency_ms": metrics.p95_latency_ms,
            "timeout_count": metrics.timeout_count,
            "incomplete_count": metrics.missing_responses,
            "note": metrics.note,
        }

    queue_handoff = bundle.queue_handoff
    queues_summary = {
        "generated_messages": queue_handoff.generated_messages if queue_handoff else 0,
        "application_queued_messages": queue_handoff.application_queued_messages if queue_handoff else 0,
        "processor_received_messages": queue_handoff.processor_received_messages if queue_handoff else 0,
        "downstream_routed_messages": queue_handoff.downstream_routed_messages if queue_handoff else 0,
        "unmatched_messages": queue_handoff.unmatched_messages if queue_handoff else 0,
        "orphan_messages": queue_handoff.orphan_messages if queue_handoff else 0,
    }

    return {"dependencies": dependencies, "queues": queues_summary}


def build_oob_duration_histogram(bundle: AnalysisBundle) -> Dict[str, Any]:
    """Supports the required OOB duration histogram -- the raw list of
    completed OOB_API pair latencies, each with its own request/response
    event ids AND join_key (the tracker/correlation id the pair was
    matched on -- see RequestResponsePair.join_key) for drill-through via
    the Investigation tracker search. Bucketing into histogram bins is
    left to the frontend (a presentation choice, not an analytical one)."""
    metrics = bundle.dependency_metrics.get("OOB_API")
    if not metrics:
        return {"dependency": "OOB_API", "samples": []}
    samples = [
        {
            "latency_ms": p.latency_ms,
            "outcome": p.outcome.value,
            "request_event_id": p.request_event_id,
            "response_event_id": p.response_event_id,
            "join_key": p.join_key,
        }
        for p in metrics.pairs
        if p.latency_ms is not None and not p.is_duplicate_response
    ]
    return {"dependency": "OOB_API", "samples": samples}


def build_dependency_p95_trend(bundle: AnalysisBundle, dependency: str, bucket_minutes: int = 60) -> Dict[str, Any]:
    """Supports the required 'dependency P95 trend' chart -- re-buckets
    the SAME pairs already computed in bundle.dependency_metrics, no new
    pairing logic."""
    from backend.analysis.dependency import compute_dependency_time_buckets
    from backend.analysis.dependency_schema import Dependency

    try:
        dep_enum = Dependency(dependency)
    except ValueError:
        return {"dependency": dependency, "buckets": []}

    report = compute_dependency_time_buckets(bundle.events, dep_enum, bucket_minutes=bucket_minutes)
    return {
        "dependency": dependency,
        "buckets": [b.model_dump() for b in report.buckets],
    }


# ---------------------------------------------------------------------------
# 3. QUEUE AND MESSAGING
# ---------------------------------------------------------------------------


def build_queue_messaging(bundle: AnalysisBundle) -> Dict[str, Any]:
    report = bundle.queue_handoff
    if not report:
        return {
            "generated": 0, "application_queued": 0, "processor_received": 0, "downstream_routed": 0,
            "validated": 0, "unmatched": 0, "orphan": 0, "queue_distribution": {}, "handoff_latency": [],
            "unmatched_tracker_nos": [], "orphan_tracker_nos": [],
        }
    return {
        "generated": report.generated_messages,
        "application_queued": report.application_queued_messages,
        "processor_received": report.processor_received_messages,
        "downstream_routed": report.downstream_routed_messages,
        "validated": report.validated_messages,  # completes the 5-stage OTP handoff funnel (Phase 5)
        "unmatched": report.unmatched_messages,
        "orphan": report.orphan_messages,
        "queue_distribution": report.queue_distribution,
        "handoff_latency": [t.model_dump() for t in report.transition_latencies],
        "unmatched_tracker_nos": report.unmatched_tracker_nos,
        "orphan_tracker_nos": report.orphan_tracker_nos,
    }


# ---------------------------------------------------------------------------
# 4. INVESTIGATION
# ---------------------------------------------------------------------------

SEARCHABLE_FIELDS = ("transaction_id", "tracker", "stepup_request_id", "mobile", "card_last4", "msg_id", "correlation_id")


def search_flows(bundle: AnalysisBundle, field: str, query: str) -> List[str]:
    """Returns matching flow_ids. `mobile` is matched by masking the
    QUERY the same way Phase 2 masks stored events (mask_mobile()) and
    comparing against the already-masked masked_mobile field -- the raw
    mobile number is never stored or compared anywhere, including here."""
    if field not in SEARCHABLE_FIELDS:
        raise ValueError(f"Unsupported search field: {field}")

    if field == "mobile":
        masked_query = mask_mobile(query)
        event_to_flow: Dict[str, str] = {}
        for flow in bundle.flows:
            for eid in flow.linked_event_ids:
                event_to_flow[eid] = flow.flow_id
        matched = {
            event_to_flow[e.source_event_id]
            for e in bundle.events
            if e.masked_mobile and e.masked_mobile == masked_query and e.source_event_id in event_to_flow
        }
        return sorted(matched)

    attr_map = {
        "transaction_id": "transaction_id",
        "tracker": "tracker_no",
        "stepup_request_id": "stepup_request_id",
        "card_last4": "card_last4",
        "msg_id": "msg_id",
        "correlation_id": "correlation_id",
    }
    attr = attr_map[field]
    matched = {f.flow_id for f in bundle.flows if getattr(f, attr, None) == query}
    return sorted(matched)


def search_flows_by_raw_text(bundle: AnalysisBundle, query: str) -> List[str]:
    """Substring match (case-insensitive) over each event's raw_reference --
    the investigation-level counterpart to Explore Events' search_term
    (backend/core/store.py's query_events()), which only returns flat event
    rows, not the correlated flow/case those events belong to. Operates
    entirely on the already-normalized bundle.events (raw_reference is kept
    there for exactly this kind of traceability), never a fresh DB query --
    same "AnalysisBundle only" contract as every other function here."""
    needle = query.strip().lower()
    if not needle:
        return []
    event_to_flow: Dict[str, str] = {}
    for flow in bundle.flows:
        for eid in flow.linked_event_ids:
            event_to_flow[eid] = flow.flow_id
    matched = {
        event_to_flow[e.source_event_id]
        for e in bundle.events
        if e.source_event_id in event_to_flow and needle in (e.raw_reference or "").lower()
    }
    return sorted(matched)


def build_investigation_result(bundle: AnalysisBundle, flow_id: str) -> Optional[Dict[str, Any]]:
    flow = next((f for f in bundle.flows if f.flow_id == flow_id), None)
    if not flow:
        return None
    lifecycle = bundle.lifecycle_by_flow_id.get(flow_id)
    if not lifecycle:
        return None

    findings = bundle.failure_result.findings if bundle.failure_result else []
    conflicts = bundle.correlation_result.conflicts if bundle.correlation_result else []
    case = build_investigation_case(flow, bundle.events, lifecycle, findings=findings, conflicts=conflicts, dependency_metrics=bundle.dependency_metrics)

    flow_findings = [f for f in findings if flow_id in f.affected_flow_ids]
    other_findings_by_flow = {
        f.flow_id: [finding for finding in findings if f.flow_id in finding.affected_flow_ids]
        for f in bundle.flows
        if f.flow_id != flow_id
    }
    similar_incidents = find_similar_incidents(flow_id, flow_findings, other_findings_by_flow)

    result = case.model_dump()
    result["similar_incident_flow_ids"] = similar_incidents
    return result


# ---------------------------------------------------------------------------
# CORRELATION EXPLORER -- surfaces Phase 3's CorrelationConflict /
# CandidateLink / LowConfidenceHint, which were computed all along but never
# exposed by any earlier dashboard (see the Phase 1-11 enterprise audit,
# finding B2: the deterministic model never hides these, but no UI showed
# them either).
# ---------------------------------------------------------------------------


def build_correlation_explorer(bundle: AnalysisBundle) -> Dict[str, Any]:
    cr = bundle.correlation_result
    if not cr:
        return {"conflicts": [], "candidate_links": [], "low_confidence_hints": [], "graph": {"nodes": [], "edges": []}}

    flow_by_id = {f.flow_id: f for f in bundle.flows}

    conflicts = [
        {
            "conflict_id": c.conflict_id,
            "triggering_key_type": c.triggering_key_type,
            "triggering_value": c.triggering_value,
            "conflicting_identifiers": [ci.model_dump() for ci in c.conflicting_identifiers],
            "affected_flow_ids": c.affected_flow_ids,
        }
        for c in cr.conflicts
    ]
    candidate_links = [
        {
            "link_type": link.link_type,
            "confidence": link.confidence.value,
            "flow_a_id": link.flow_a_id,
            "flow_b_id": link.flow_b_id,
            "matching_keys": link.matching_keys,
            "note": link.note,
        }
        for link in cr.candidate_links
    ]
    low_confidence_hints = [
        {"hint_type": h.hint_type, "value": h.value, "flow_ids": h.flow_ids, "note": h.note}
        for h in cr.low_confidence_hints
    ]

    node_ids: set = set()
    edges: List[Dict[str, Any]] = []
    for c in cr.conflicts:
        node_ids.update(c.affected_flow_ids)
        if len(c.affected_flow_ids) >= 2:
            edges.append(
                {"source": c.affected_flow_ids[0], "target": c.affected_flow_ids[1], "edge_type": "conflict", "label": c.triggering_key_type}
            )
    for link in cr.candidate_links:
        node_ids.update([link.flow_a_id, link.flow_b_id])
        edges.append({"source": link.flow_a_id, "target": link.flow_b_id, "edge_type": "candidate_link", "label": link.link_type})
    for h in cr.low_confidence_hints:
        node_ids.update(h.flow_ids)
        # A hint can name >2 flows sharing one masked value -- fan out from
        # the first flow to every other one rather than a full clique, to
        # keep the edge count linear in flow count, not quadratic.
        if len(h.flow_ids) >= 2:
            anchor = h.flow_ids[0]
            for other in h.flow_ids[1:]:
                edges.append({"source": anchor, "target": other, "edge_type": "low_confidence_hint", "label": h.hint_type})

    nodes = [
        {
            "flow_id": fid,
            "transaction_id": flow_by_id[fid].transaction_id if fid in flow_by_id else None,
            "correlation_status": flow_by_id[fid].correlation_status.value if fid in flow_by_id else None,
        }
        for fid in sorted(node_ids)
    ]

    return {
        "conflicts": conflicts,
        "candidate_links": candidate_links,
        "low_confidence_hints": low_confidence_hints,
        "graph": {"nodes": nodes, "edges": edges},
    }


# ---------------------------------------------------------------------------
# 5. SECURITY / DATA QUALITY
# ---------------------------------------------------------------------------


def build_security_quality(bundle: AnalysisBundle) -> Dict[str, Any]:
    qr = bundle.quality_result
    if not qr:
        return {}

    repeated_attempts = sum(1 for f in bundle.flows if f.duplicate_event_ids)

    # Field mismatch heatmap: field_name -> MISMATCH count, with the
    # flow_ids that exhibited it for drill-through.
    mismatch_by_field: Dict[str, List[str]] = defaultdict(list)
    for r in qr.field_consistency:
        if r.status.value == "MISMATCH":
            mismatch_by_field[r.field_name].append(r.flow_id)
    field_mismatch_heatmap = [
        {"field_name": field, "mismatch_count": len(flow_ids), "sample_flow_ids": flow_ids[:_MAX_SAMPLE_FLOW_IDS]}
        for field, flow_ids in sorted(mismatch_by_field.items(), key=lambda kv: -len(kv[1]))
    ]

    return {
        "scorecard": qr.scorecard.model_dump(),
        "correlation_quality_breakdown": qr.correlation_quality_breakdown.model_dump(),
        "field_consistency_exceptions": [r.model_dump() for r in qr.field_consistency if r.status.value != "CONSISTENT"],
        "field_mismatch_heatmap": field_mismatch_heatmap,
        "sensitive_data_findings": [f.model_dump() for f in qr.sensitive_data_findings],
        "repeated_attempts": repeated_attempts,
        "incomplete_flows": qr.scorecard.flow_classification_counts.get("PARTIAL", 0),
        "uncorrelated_flows": qr.scorecard.flow_classification_counts.get("UNCORRELATED", 0),
        "exception_table": [e.model_dump() for e in qr.exception_table],
    }
