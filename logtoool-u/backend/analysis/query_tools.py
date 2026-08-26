"""
AI Analyst Deterministic Query Tools -- Phase 11 of the LLens Multi-Log
Analysis Strategy.

A FIXED, closed registry of read-only functions over an AnalysisBundle
(Phases 2-10). This is the ONLY interface the user-facing AI Analyst
(backend/llm/ai_analyst.py) is allowed to call: the LLM selects a tool name
and parameters from TOOL_SPECS below, but every actual computation,
aggregation, and metric in this file is plain Python over already-computed
Phase 2-10 output -- no SQL, no join, and no metric is ever invented by the
model. This is what "the AI must not independently invent SQL, joins,
metrics or conclusions" means structurally, not just as a prompt rule.

Two tools here (issuer_failure_rates, list_flows_by_status) are new
aggregations not exposed by any earlier phase's dashboard -- both are
straightforward re-aggregations of already-computed CorrelatedFlow/
FlowLifecycle records (Phases 3-4), with no new correlation, joining, or
classification logic of their own.
"""
from typing import Any, Callable, Dict, List, Optional

from backend.analysis.dashboards import SEARCHABLE_FIELDS, build_investigation_result, search_flows
from backend.analysis.pipeline import AnalysisBundle

_TERMINAL_STATUS_VALUES = {"SUCCESS", "FAILED", "INCOMPLETE", "PENDING_AT_LOG_END", "UNDETERMINED"}
_AUTH_TEMPLATE_VALUES = {"OTP", "OOB"}
_MAX_LIST_SAMPLE = 50


def lookup_transaction(bundle: AnalysisBundle, field: str, value: str) -> Dict[str, Any]:
    """Exact-identifier lookup. Reuses Phase 10's search_flows +
    build_investigation_result untouched, so this tool's answer is always
    identical to the case the Investigation dashboard would show for the
    same identifier."""
    if field not in SEARCHABLE_FIELDS:
        return {
            "tool": "lookup_transaction",
            "error": f"Unsupported field '{field}'. Supported: {list(SEARCHABLE_FIELDS)}",
            "match_count": 0,
            "matches": [],
        }
    flow_ids = search_flows(bundle, field, value)
    results = [build_investigation_result(bundle, fid) for fid in flow_ids]
    return {
        "tool": "lookup_transaction",
        "field": field,
        "value": value,
        "match_count": len(flow_ids),
        "matches": [r for r in results if r],
    }


def _dependency_summary(m) -> Dict[str, Any]:
    return {
        "request_count": m.request_count,
        "completed_responses": m.completed_responses,
        "missing_responses": m.missing_responses,
        "successful_responses": m.successful_responses,
        "error_rate": m.error_rate,
        "median_latency_ms": m.median_latency_ms,
        "p95_latency_ms": m.p95_latency_ms,
        "timeout_count": m.timeout_count,
        "delayed_count": m.delayed_count,
        "expected_latency_ms": m.expected_latency_ms,
        "duplicate_responses": m.duplicate_responses,
        "failure_signatures": m.failure_signatures,
        "note": m.note,
        "sample_pairs": [
            {
                "request_event_id": p.request_event_id,
                "response_event_id": p.response_event_id,
                "join_key": p.join_key,
                "outcome": p.outcome.value,
                "latency_ms": p.latency_ms,
            }
            for p in m.pairs[:5]
        ],
    }


def dependency_health_tool(bundle: AnalysisBundle, dependency: Optional[str] = None) -> Dict[str, Any]:
    metrics_by_name = bundle.dependency_metrics
    if dependency:
        m = metrics_by_name.get(dependency)
        if not m:
            return {
                "tool": "dependency_health",
                "dependency": dependency,
                "error": f"No data for dependency '{dependency}' in the analyzed window.",
                "metrics": None,
            }
        return {"tool": "dependency_health", "dependency": dependency, "metrics": _dependency_summary(m)}
    return {
        "tool": "dependency_health",
        "dependency": None,
        "metrics": {name: _dependency_summary(m) for name, m in metrics_by_name.items()},
    }


def queue_handoff_tool(bundle: AnalysisBundle) -> Dict[str, Any]:
    report = bundle.queue_handoff
    if not report:
        return {"tool": "queue_handoff_health", "report": None}
    return {
        "tool": "queue_handoff_health",
        "report": {
            "generated_messages": report.generated_messages,
            "application_queued_messages": report.application_queued_messages,
            "processor_received_messages": report.processor_received_messages,
            "downstream_routed_messages": report.downstream_routed_messages,
            "validated_messages": report.validated_messages,
            "unmatched_messages": report.unmatched_messages,
            "unmatched_tracker_nos": report.unmatched_tracker_nos[:20],
            "orphan_messages": report.orphan_messages,
            "orphan_tracker_nos": report.orphan_tracker_nos[:20],
            "queue_distribution": report.queue_distribution,
            "transition_latencies": [t.model_dump() for t in report.transition_latencies],
        },
    }


def top_failures_tool(bundle: AnalysisBundle, limit: int = 10) -> Dict[str, Any]:
    pr = bundle.pattern_result
    if not pr or not pr.patterns:
        return {"tool": "top_failures", "total_flows_analyzed": pr.total_flows_analyzed if pr else 0, "patterns": []}
    bounded_limit = max(1, min(int(limit), 25))
    top = pr.patterns[:bounded_limit]
    return {
        "tool": "top_failures",
        "total_flows_analyzed": pr.total_flows_analyzed,
        "patterns": [
            {
                "pattern": p.pattern,
                "rank": p.rank,
                "affected_flows": p.affected_flows,
                "total_flows": p.total_flows,
                "failure_rate": p.failure_rate,
                "low_sample_warning": p.low_sample_warning,
                "first_seen": p.first_seen,
                "last_seen": p.last_seen,
                "affected_issuers": p.affected_issuers,
                "affected_dependencies": p.affected_dependencies,
                "representative_evidence": [e.model_dump() for e in p.representative_evidence],
            }
            for p in top
        ],
    }


def recurring_incidents_tool(bundle: AnalysisBundle, min_affected_flows: int = 2) -> Dict[str, Any]:
    pr = bundle.pattern_result
    if not pr:
        return {"tool": "recurring_incidents", "total_flows_analyzed": 0, "recurring_patterns": []}
    threshold = max(1, int(min_affected_flows))
    recurring = [p for p in pr.patterns if p.affected_flows >= threshold]
    return {
        "tool": "recurring_incidents",
        "total_flows_analyzed": pr.total_flows_analyzed,
        "min_affected_flows_threshold": threshold,
        "recurring_patterns": [
            {
                "pattern": p.pattern,
                "affected_flows": p.affected_flows,
                "total_flows": p.total_flows,
                "failure_rate": p.failure_rate,
                "first_seen": p.first_seen,
                "last_seen": p.last_seen,
                "low_sample_warning": p.low_sample_warning,
            }
            for p in recurring
        ],
    }


def correlation_quality_tool(bundle: AnalysisBundle, flow_id: Optional[str] = None) -> Dict[str, Any]:
    """Correlation conflicts/candidate-links/low-confidence-hints, straight
    from bundle.correlation_result -- same object build_correlation_explorer
    (backend/analysis/dashboards.py) reshapes for the Correlation Explorer
    UI, so this tool's answer is always consistent with what that graph
    shows. No new correlation math: conflicting_identifiers, matching_keys,
    and source_event_ids are already fully computed by correlate_events()
    (backend/analysis/correlate.py) and just weren't surfaced here before.

    Pass flow_id to filter every category down to only the entries touching
    that one flow (its flow_id or transaction_id) -- this is what answers
    "why was X flagged as conflicting" / "why were X and Y linked", since
    filtering by one endpoint of a conflict/link/hint returns it regardless
    of which flow's id the caller happened to supply."""
    cr = bundle.correlation_result
    qr = bundle.quality_result
    conflicts = cr.conflicts if cr else []
    candidate_links = cr.candidate_links if cr else []
    low_conf_hints = cr.low_confidence_hints if cr else []

    if flow_id:
        conflicts = [c for c in conflicts if flow_id in c.affected_flow_ids]
        candidate_links = [l for l in candidate_links if flow_id in (l.flow_a_id, l.flow_b_id)]
        low_conf_hints = [h for h in low_conf_hints if flow_id in h.flow_ids]

    return {
        "tool": "correlation_quality",
        "flow_id": flow_id,
        "conflict_count": len(conflicts),
        "conflicts": [
            {
                "conflict_id": c.conflict_id,
                "triggering_key_type": c.triggering_key_type,
                "triggering_value": c.triggering_value,
                "affected_flow_ids": c.affected_flow_ids,
                "conflicting_identifiers": [ci.model_dump() for ci in c.conflicting_identifiers],
                "source_event_ids_a": c.source_event_ids_a,
                "source_event_ids_b": c.source_event_ids_b,
            }
            for c in conflicts[:20]
        ],
        "candidate_link_count": len(candidate_links),
        "candidate_links": [
            {
                "link_type": l.link_type,
                "confidence": l.confidence.value,
                "flow_a_id": l.flow_a_id,
                "flow_b_id": l.flow_b_id,
                "matching_keys": l.matching_keys,
                "note": l.note,
            }
            for l in candidate_links[:20]
        ],
        "low_confidence_hint_count": len(low_conf_hints),
        "low_confidence_hints": [
            {
                "hint_type": h.hint_type,
                "value": h.value,
                "flow_ids": h.flow_ids,
                "note": h.note,
            }
            for h in low_conf_hints[:20]
        ],
        "correlation_quality_breakdown": qr.correlation_quality_breakdown.model_dump() if qr else {},
    }


def issuer_failure_rates_tool(bundle: AnalysisBundle) -> Dict[str, Any]:
    """Per-issuer total correlated flows vs. FAILED flows, computed purely
    from bundle.flows/bundle.lifecycle_by_flow_id -- no raw events touched,
    no new correlation or joins performed. Not exposed by any earlier
    phase's dashboard as a standalone ranking, but uses only fields those
    phases already computed."""
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
    return {"tool": "issuer_failure_rates", "issuers": rates}


def list_flows_by_status_tool(
    bundle: AnalysisBundle,
    terminal_status: Optional[str] = None,
    auth_template: Optional[str] = None,
) -> Dict[str, Any]:
    """Filters already-computed FlowLifecycle records (Phase 4) by terminal
    status and/or auth template. Supports "show me incomplete OOB
    transactions" style questions that need a LIST of matching flows,
    not a single lookup or an aggregate metric -- no new classification
    logic, purely filtering."""
    if terminal_status and terminal_status not in _TERMINAL_STATUS_VALUES:
        return {
            "tool": "list_flows_by_status",
            "error": f"Unsupported terminal_status '{terminal_status}'. Supported: {sorted(_TERMINAL_STATUS_VALUES)}",
            "match_count": 0,
            "flows": [],
        }
    if auth_template and auth_template not in _AUTH_TEMPLATE_VALUES:
        return {
            "tool": "list_flows_by_status",
            "error": f"Unsupported auth_template '{auth_template}'. Supported: {sorted(_AUTH_TEMPLATE_VALUES)}",
            "match_count": 0,
            "flows": [],
        }

    matches = [
        lc
        for lc in bundle.lifecycles
        if (not terminal_status or lc.terminal_status.value == terminal_status)
        and (not auth_template or (lc.auth_template and lc.auth_template.value == auth_template))
    ]
    flow_by_id = {f.flow_id: f for f in bundle.flows}

    return {
        "tool": "list_flows_by_status",
        "terminal_status": terminal_status,
        "auth_template": auth_template,
        "match_count": len(matches),
        "flows": [
            {
                "flow_id": lc.flow_id,
                "transaction_id": flow_by_id[lc.flow_id].transaction_id if lc.flow_id in flow_by_id else None,
                "terminal_status": lc.terminal_status.value,
                "auth_template": lc.auth_template.value if lc.auth_template else None,
                "last_confirmed_stage": lc.last_confirmed_stage,
                "missing_next_stage": lc.missing_next_stage,
            }
            for lc in matches[:_MAX_LIST_SAMPLE]
        ],
    }


TOOL_REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "lookup_transaction": lookup_transaction,
    "dependency_health": dependency_health_tool,
    "queue_handoff_health": queue_handoff_tool,
    "top_failures": top_failures_tool,
    "recurring_incidents": recurring_incidents_tool,
    "correlation_quality": correlation_quality_tool,
    "issuer_failure_rates": issuer_failure_rates_tool,
    "list_flows_by_status": list_flows_by_status_tool,
}

# Descriptions + parameter contracts shown to the LLM for tool SELECTION
# only -- the LLM never sees anything beyond this list and cannot invent a
# 9th tool or a parameter name not listed here (see
# backend/llm/ai_analyst.py's _select_tool()/_sanitize_params()).
TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "lookup_transaction",
        "description": (
            "Look up one transaction/flow by an exact identifier and return its full case (timeline, "
            "findings, correlation, lifecycle stage). Use for 'what happened to transaction X', "
            "'why did OTP fail for X', 'where did transaction X stop'."
        ),
        "parameters": {"field": f"one of {list(SEARCHABLE_FIELDS)}", "value": "the identifier value from the question"},
    },
    {
        "name": "dependency_health",
        "description": (
            "Health metrics (latency, error rate, timeouts, delayed responses) for one dependency or all "
            "of them. Use for 'is V+ having issues', 'how many V+ responses were delayed', 'which "
            "dependency has the highest latency'."
        ),
        "parameters": {
            "dependency": "optional, one of V_PLUS|POSTILION|DATABASE_SQL|BANK_API|OOB_API|OTP_ONLINE_PROCESSOR, omit for all"
        },
    },
    {
        "name": "queue_handoff_health",
        "description": (
            "OTP application-queue -> processor -> downstream-queue handoff chain counts, including "
            "unmatched (generated but never received by the processor) and orphan trackers. Use for "
            "'are there queue handoff problems', 'show transactions where OTP was generated but never "
            "received by the processor'."
        ),
        "parameters": {},
    },
    {
        "name": "top_failures",
        "description": "Ranked list of the top recurring failure signatures with affected/total flow counts. Use for 'what are the top failures today'.",
        "parameters": {"limit": "optional integer, default 10"},
    },
    {
        "name": "recurring_incidents",
        "description": "Failure patterns that recurred across multiple flows. Use for 'are there recurring incidents'.",
        "parameters": {"min_affected_flows": "optional integer, default 2"},
    },
    {
        "name": "correlation_quality",
        "description": (
            "Correlation conflicts, candidate links, and low-confidence hints from the cross-log correlation "
            "engine, including the exact mismatched or matching field values that explain WHY flows were "
            "flagged. Use for 'are there correlation problems in the logs', 'why was transaction X flagged as "
            "conflicting', 'why were X and Y linked'. Pass flow_id (a flow_id or transaction_id from the "
            "question) to filter down to only the conflicts/links/hints involving that one flow; omit it for "
            "an overall summary."
        ),
        "parameters": {"flow_id": "optional, a specific flow_id or transaction_id to filter to"},
    },
    {
        "name": "issuer_failure_rates",
        "description": "Per-issuer total flow count, failed flow count, and failure rate. Use for 'which issuer has the highest failure rate'.",
        "parameters": {},
    },
    {
        "name": "list_flows_by_status",
        "description": (
            "List flows matching a lifecycle terminal_status (SUCCESS|FAILED|INCOMPLETE|"
            "PENDING_AT_LOG_END|UNDETERMINED) and/or auth_template (OTP|OOB). Use for 'show me incomplete "
            "OOB transactions'."
        ),
        "parameters": {"terminal_status": "optional", "auth_template": "optional"},
    },
]

TOOL_PARAM_ALLOWLIST: Dict[str, set] = {spec["name"]: set(spec["parameters"].keys()) for spec in TOOL_SPECS}


def result_has_no_evidence(tool_name: str, result: Dict[str, Any]) -> bool:
    """Deterministic check the orchestration layer uses to force
    confidence=LOW and the exact "Not found in the captured logs." phrase
    regardless of what the LLM's narration says -- never trust the model
    to notice an empty result on its own."""
    if result.get("error"):
        return True
    checks: Dict[str, Callable[[dict], bool]] = {
        "lookup_transaction": lambda r: r.get("match_count", 0) == 0,
        "dependency_health": lambda r: not r.get("metrics"),
        "queue_handoff_health": lambda r: not r.get("report"),
        "top_failures": lambda r: not r.get("patterns"),
        "recurring_incidents": lambda r: not r.get("recurring_patterns"),
        "correlation_quality": lambda r: (
            r.get("conflict_count", 0) == 0
            and r.get("candidate_link_count", 0) == 0
            and r.get("low_confidence_hint_count", 0) == 0
        ),
        "issuer_failure_rates": lambda r: not r.get("issuers"),
        "list_flows_by_status": lambda r: r.get("match_count", 0) == 0,
    }
    check = checks.get(tool_name)
    return bool(check and check(result))
