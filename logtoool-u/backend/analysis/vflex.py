"""
Aggregation analysis for the VFlex StepUp/Bank API/OTP log format (see
parser_VFlex.py).

Deliberately downstream of, and read-only against, both the parser's own
output and the stored canonical events it produces. Each event's
`attributes.details.transaction` (when present) is the parser adapter's
resolved tracker-record snapshot -- this module reads that plus
`attributes.details.parse_status` to build cross-event aggregations, it
doesn't re-parse or re-extract anything.
"""
from collections import Counter
from typing import Any, Dict, List

DEFAULT_SOURCE_SYSTEM = "vflex_transaction_log"

_MAX_FAILED_ITEMS = 100
_TOP_MERCHANTS_LIMIT = 15
_TOP_FAILURE_REASONS_LIMIT = 10


def _transactions_by_correlation(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Every event for a given correlation_id carries an identical snapshot
    of that tracker record's resolved state (see parser_VFlex.py's
    parse_log_file adapter) -- so the first one seen per correlation_id is
    enough."""
    records: Dict[str, Dict[str, Any]] = {}
    for e in events:
        attrs = e.get("attributes") or {}
        corr_id = attrs.get("correlation_id")
        tx = (attrs.get("details") or {}).get("transaction")
        if corr_id and tx and corr_id not in records:
            records[corr_id] = tx
    return records


def compute_vflex_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Issuer, status, bank-operation, and OTP-channel distribution across
    resolved tracker records, plus a breakdown of events that failed to
    parse or came through at ERROR level."""
    if not events:
        return {"status": "no_data", "message": "No VFlex activity found in the analyzed window."}

    event_type_counts = Counter(e.get("component") or "unknown" for e in events)

    transactions = _transactions_by_correlation(events)
    total_records = len(transactions)

    by_issuer: Counter = Counter()
    by_status: Counter = Counter()
    by_bank_operation: Counter = Counter()
    by_channel: Counter = Counter()
    by_merchant: Counter = Counter()
    otp_processed_count = 0
    bank_api_success_count = 0
    checks_needed = 0

    for tx in transactions.values():
        by_issuer[tx.get("issuer_id") or "UNKNOWN"] += 1
        by_status[tx.get("status") or tx.get("integrity_status") or "UNKNOWN"] += 1
        by_bank_operation[(tx.get("bank_api") or {}).get("operation") or "UNKNOWN"] += 1
        by_channel[(tx.get("otp") or {}).get("channel") or "UNKNOWN"] += 1
        by_merchant[(tx.get("merchant") or {}).get("name") or "UNKNOWN"] += 1
        if (tx.get("otp") or {}).get("processed_successfully"):
            otp_processed_count += 1
        if (tx.get("bank_api") or {}).get("success") is True:
            bank_api_success_count += 1
        if tx.get("integrity_status") == "CHECK":
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
        "total_records": total_records,
        "event_type_counts": dict(event_type_counts.most_common()),
        "by_issuer": dict(by_issuer.most_common()),
        "by_status": dict(by_status.most_common()),
        "by_bank_operation": dict(by_bank_operation.most_common()),
        "by_channel": dict(by_channel.most_common()),
        "top_merchants": dict(by_merchant.most_common(_TOP_MERCHANTS_LIMIT)),
        "otp_processed_count": otp_processed_count,
        "bank_api_success_count": bank_api_success_count,
        "checks_needed_count": checks_needed,
        "failed_events": {
            "count": len(failed_items),
            "reason_counts": dict(failed_reason_counts.most_common(_TOP_FAILURE_REASONS_LIMIT)),
            "items": failed_items[:_MAX_FAILED_ITEMS],
        },
    }
