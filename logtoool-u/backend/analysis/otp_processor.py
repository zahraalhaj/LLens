"""
Aggregation analysis for the OTP Online Processor log format (SMS/Email XML).

Deliberately downstream of, and read-only against, both the parser's own
output (parser_OTP_Processor.py -- untouched) and the stored canonical
events it produces. Each event's `attributes.details.record` (when present)
is the parser adapter's final merged per-tracker OTP record -- this module
just reads that plus `attributes.details.parse_error` to build cross-event
aggregations, it doesn't re-parse or re-extract anything.

All timestamps handled here are LLens's own normalized `ts_utc` strings
("%Y-%m-%dT%H:%M:%SZ", always UTC -- see core/timezones.py), not the raw
log timestamps.
"""
from collections import Counter
from typing import Any, Dict, List

# Matches parser_OTP_Processor.py's DEFAULT_SOURCE_SYSTEM -- kept here too
# (not imported from the parser module) so this analysis module has no
# dependency on the custom_parsers package, just a shared convention.
DEFAULT_SOURCE_SYSTEM = "otp_online_processor"

_MAX_FAILED_ITEMS = 100
_TOP_MERCHANTS_LIMIT = 15
_TOP_FAILURE_REASONS_LIMIT = 10


def _records_by_tracker(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Each event for a given tracker carries an identical snapshot of that
    tracker's final merged record (see parser_OTP_Processor.py's
    parse_log_file adapter) -- so the first one seen per tracker is enough,
    no need to re-merge anything here."""
    records: Dict[str, Dict[str, Any]] = {}
    for e in events:
        attrs = e.get("attributes") or {}
        corr_id = attrs.get("correlation_id")
        record = (attrs.get("details") or {}).get("record")
        if corr_id and record and corr_id not in records:
            records[corr_id] = record
    return records


def compute_otp_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Queue/aggregator, merchant, and org distribution across resolved OTP
    transactions, plus a breakdown of events that failed to parse or didn't
    match any known event shape (surfaced, not discarded -- consistent with
    the parser's own "no line is dropped" convention)."""
    if not events:
        return {"status": "no_data", "message": "No OTP processor activity found in the analyzed window."}

    event_type_counts = Counter(e.get("component") or "unknown" for e in events)

    records_by_tracker = _records_by_tracker(events)
    total_records = len(records_by_tracker)

    by_org: Counter = Counter()
    by_queue: Counter = Counter()
    by_currency: Counter = Counter()
    by_merchant: Counter = Counter()
    otp_processed_count = 0
    force_verify_count = 0

    for record in records_by_tracker.values():
        by_org[record.get("org") or "UNKNOWN"] += 1
        by_queue[record.get("queue") or "UNKNOWN"] += 1
        by_currency[(record.get("transaction") or {}).get("currency") or "UNKNOWN"] += 1
        by_merchant[record.get("merchant") or "UNKNOWN"] += 1
        if record.get("otp_processed"):
            otp_processed_count += 1
        if record.get("force_verify_by_mobile"):
            force_verify_count += 1

    failed_items = []
    failed_reason_counts: Counter = Counter()
    for e in events:
        attrs = e.get("attributes") or {}
        details = attrs.get("details") or {}
        reason = details.get("parse_error")
        if not reason:
            continue
        failed_reason_counts[reason[:120]] += 1
        failed_items.append(
            {
                "timestamp": e.get("ts_utc"),
                "tracker_no": details.get("tracker_no"),
                "reason": reason,
                "message": e.get("message"),
            }
        )

    return {
        "status": "ok",
        "window_start": events[0].get("ts_utc"),
        "window_end": events[-1].get("ts_utc"),
        "total_events_analyzed": len(events),
        "total_records": total_records,
        "event_type_counts": dict(event_type_counts.most_common()),
        "by_org": dict(by_org.most_common()),
        "by_queue": dict(by_queue.most_common()),
        "by_currency": dict(by_currency.most_common()),
        "top_merchants": dict(by_merchant.most_common(_TOP_MERCHANTS_LIMIT)),
        "otp_processed_count": otp_processed_count,
        "otp_success_rate_pct": round(100 * otp_processed_count / total_records, 1) if total_records else None,
        "force_verify_count": force_verify_count,
        "failed_events": {
            "count": len(failed_items),
            "reason_counts": dict(failed_reason_counts.most_common(_TOP_FAILURE_REASONS_LIMIT)),
            "items": failed_items[:_MAX_FAILED_ITEMS],
        },
    }
