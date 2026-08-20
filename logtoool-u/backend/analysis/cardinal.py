"""
Aggregation analysis for the Cardinal OTP/StepUp/OOB log format (see
parser_Cardinal.py).

Deliberately downstream of, and read-only against, both the parser's own
output and the stored canonical events it produces. Each event's
`attributes.details.flow` (when present) is the parser adapter's resolved
flow snapshot -- this module reads that plus `attributes.details.parse_status`
to build cross-event aggregations, it doesn't re-parse or re-extract
anything.
"""
from collections import Counter
from typing import Any, Dict, List

DEFAULT_SOURCE_SYSTEM = "cardinal_stepup_oob_log"

_MAX_FAILED_ITEMS = 100
_TOP_MERCHANTS_LIMIT = 15
_TOP_FAILURE_REASONS_LIMIT = 10


def _flows_by_correlation(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Every event for a given correlation_id carries an identical snapshot
    of that flow's resolved state (see parser_Cardinal.py's parse_log_file
    adapter) -- so the first one seen per correlation_id is enough."""
    flows: Dict[str, Dict[str, Any]] = {}
    for e in events:
        attrs = e.get("attributes") or {}
        corr_id = attrs.get("correlation_id")
        flow = (attrs.get("details") or {}).get("flow")
        if corr_id and flow and corr_id not in flows:
            flows[corr_id] = flow
    return flows


def compute_cardinal_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Issuer, authentication-status, and bank-org distribution across
    resolved flows, plus OOB status history and a breakdown of events that
    failed to parse or came through at ERROR level."""
    if not events:
        return {"status": "no_data", "message": "No Cardinal activity found in the analyzed window."}

    event_type_counts = Counter(e.get("component") or "unknown" for e in events)

    flows = _flows_by_correlation(events)
    total_flows = len(flows)

    by_issuer: Counter = Counter()
    by_status: Counter = Counter()
    by_bank_org: Counter = Counter()
    by_merchant: Counter = Counter()
    oob_status_counts: Counter = Counter()
    otp_processed_count = 0
    checks_needed = 0

    for flow in flows.values():
        by_issuer[flow.get("issuer_id") or "UNKNOWN"] += 1
        auth = flow.get("authentication") or {}
        by_status[auth.get("status") or flow.get("integrity_status") or "UNKNOWN"] += 1
        by_bank_org[flow.get("bank_org") or "UNKNOWN"] += 1
        by_merchant[(flow.get("merchant") or {}).get("name") or "UNKNOWN"] += 1
        for status_value in (flow.get("oob") or {}).get("status_history") or []:
            oob_status_counts[status_value] += 1
        if auth.get("otp_processed"):
            otp_processed_count += 1
        if flow.get("integrity_status") == "CHECK":
            checks_needed += 1

    failed_items = []
    failed_reason_counts: Counter = Counter()
    for e in events:
        attrs = e.get("attributes") or {}
        details = attrs.get("details") or {}
        is_error_level = e.get("level") == "ERROR"
        is_partial = details.get("parse_status") == "partial"
        if not (is_error_level or is_partial):
            continue
        warnings = details.get("warnings") or []
        reason = (warnings[0] if warnings else None) or e.get("message") or "Error event"
        reason = reason[:120]
        failed_reason_counts[reason] += 1
        failed_items.append(
            {
                "timestamp": e.get("ts_utc"),
                "correlation_id": attrs.get("correlation_id"),
                "reason": reason,
                "message": e.get("message"),
            }
        )

    return {
        "status": "ok",
        "window_start": events[0].get("ts_utc"),
        "window_end": events[-1].get("ts_utc"),
        "total_events_analyzed": len(events),
        "total_flows": total_flows,
        "event_type_counts": dict(event_type_counts.most_common()),
        "by_issuer": dict(by_issuer.most_common()),
        "by_status": dict(by_status.most_common()),
        "by_bank_org": dict(by_bank_org.most_common()),
        "oob_status_counts": dict(oob_status_counts.most_common()),
        "top_merchants": dict(by_merchant.most_common(_TOP_MERCHANTS_LIMIT)),
        "otp_processed_count": otp_processed_count,
        "checks_needed_count": checks_needed,
        "failed_events": {
            "count": len(failed_items),
            "reason_counts": dict(failed_reason_counts.most_common(_TOP_FAILURE_REASONS_LIMIT)),
            "items": failed_items[:_MAX_FAILED_ITEMS],
        },
    }
