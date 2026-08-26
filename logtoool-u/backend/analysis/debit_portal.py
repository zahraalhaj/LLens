"""
Aggregation analysis for the Debit Portal log format (transactions + errors,
see parser_Debit_Transaction.py).

Deliberately downstream of, and read-only against, both the parser's own
output and the stored canonical events it produces. Each event's
`attributes.details.transaction` (when present) is the parser adapter's
resolved transaction snapshot -- this module reads that plus
`attributes.details.parse_status`/`parse_warning` to build cross-event
aggregations, it doesn't re-parse or re-extract anything.
"""
from collections import Counter
from typing import Any, Dict, List, Optional

from backend.analysis.normalized_schema import (
    LogFamily,
    NormalizedEvent,
    build_failure_signature,
    classify_normalized_stage,
    derive_tracker_type_and_phase,
    extract_card_last4,
    looks_like_tracker,
    mask_email,
    mask_mobile,
)
from backend.core.currency import resolve_currency_code

DEFAULT_SOURCE_SYSTEM = "debit_portal_log"

_MAX_FAILED_ITEMS = 100
_TOP_MERCHANTS_LIMIT = 15
_TOP_FAILURE_REASONS_LIMIT = 10


def extract_merchant_name(event: Dict[str, Any]) -> Optional[str]:
    """The merchant name embedded in this event's transaction snapshot, for
    the by-merchant filter -- same accessor `compute_debit_portal_summary`
    uses to build `top_merchants`, just exposed for pre-aggregation
    filtering."""
    tx = ((event.get("attributes") or {}).get("details") or {}).get("transaction") or {}
    return (tx.get("merchant") or {}).get("name")


def _transactions_by_correlation(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Every event for a given correlation_id carries an identical snapshot
    of that transaction's resolved state (see parser_Debit_Transaction.py's
    parse_log_file adapter) -- so the first one seen per correlation_id is
    enough, no need to re-merge anything here."""
    records: Dict[str, Dict[str, Any]] = {}
    for e in events:
        attrs = e.get("attributes") or {}
        corr_id = attrs.get("correlation_id")
        tx = (attrs.get("details") or {}).get("transaction")
        if corr_id and tx and corr_id not in records:
            records[corr_id] = tx
    return records


def compute_debit_portal_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Issuer, status, and merchant distribution across resolved
    transactions, plus a breakdown of events that failed to parse or came
    through at ERROR level."""
    if not events:
        return {"status": "no_data", "message": "No Debit Portal activity found in the analyzed window."}

    event_type_counts = Counter(e.get("component") or "unknown" for e in events)

    transactions = _transactions_by_correlation(events)
    total_records = len(transactions)

    by_issuer: Counter = Counter()
    by_status: Counter = Counter()
    by_currency: Counter = Counter()
    by_merchant: Counter = Counter()
    otp_processed_count = 0
    checks_needed = 0

    for tx in transactions.values():
        by_issuer[tx.get("issuer_id") or "UNKNOWN"] += 1
        by_status[tx.get("status") or tx.get("integrity_status") or "UNKNOWN"] += 1
        by_currency[resolve_currency_code((tx.get("transaction") or {}).get("currency")) or "UNKNOWN"] += 1
        by_merchant[(tx.get("merchant") or {}).get("name") or "UNKNOWN"] += 1
        if tx.get("otp_processed"):
            otp_processed_count += 1
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
        reason = details.get("parse_warning") or e.get("message") or "Error event"
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
        "by_currency": dict(by_currency.most_common()),
        "top_merchants": dict(by_merchant.most_common(_TOP_MERCHANTS_LIMIT)),
        "otp_processed_count": otp_processed_count,
        "checks_needed_count": checks_needed,
        "failed_events": {
            "count": len(failed_items),
            "reason_counts": dict(failed_reason_counts.most_common(_TOP_FAILURE_REASONS_LIMIT)),
            "items": failed_items[:_MAX_FAILED_ITEMS],
        },
    }


def normalize_debit_portal_event(event: Dict[str, Any]) -> NormalizedEvent:
    """Maps one stored Debit Portal CanonicalLogEvent into the common
    NormalizedEvent shape (see backend/analysis/normalized_schema.py).
    Reads `attributes.details.transaction` (the resolved snapshot, once a
    transaction has been correlated) and falls back to the per-event
    `attributes.details.parsed` payload for events that haven't resolved to
    a transaction yet (e.g. an isolated partial event)."""
    attrs = event.get("attributes") or {}
    details = attrs.get("details") or {}
    tx = details.get("transaction") or {}
    parsed = details.get("parsed") or {}
    correlation_id = attrs.get("correlation_id")

    trackers = tx.get("trackers") or []
    tracker_no = (trackers[0] if trackers else None) or parsed.get("tracker_no")
    if not tracker_no and looks_like_tracker(correlation_id):
        tracker_no = correlation_id
    tracker_type, phase = derive_tracker_type_and_phase(tracker_no)

    transaction_id = tx.get("transaction_id")
    customer = tx.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    mobile = customer.get("mobile") or parsed.get("mobile")
    email = customer.get("email") or parsed.get("email") or parsed.get("email_to")
    merchant = tx.get("merchant") or {}
    if not isinstance(merchant, dict):
        merchant = {}
    transaction_info = tx.get("transaction") or parsed.get("transaction") or {}
    if not isinstance(transaction_info, dict):
        transaction_info = {}
    queue = tx.get("queue")

    sensitive_removed: List[str] = []
    if mobile:
        sensitive_removed.append("mobile")
    if email:
        sensitive_removed.append("email")
    if parsed.get("otp"):
        sensitive_removed.append("parsed.otp")
    if parsed.get("otppan"):
        sensitive_removed.append("parsed.otppan")

    level = event.get("level")
    component = event.get("component")
    parse_status = details.get("parse_status") or "parsed"
    warnings = tx.get("warnings") or []
    failure_sig = None
    if level in ("ERROR", "CRITICAL") or parse_status == "partial":
        reason = (
            details.get("parse_warning")
            or (warnings[0] if warnings else None)
            or event.get("message")
            or "unknown_error"
        )
        failure_sig = build_failure_signature(LogFamily.DEBIT_PORTAL, component, reason)

    confidence = 1.0 if transaction_id else (0.5 if tracker_no else 0.2)
    error = tx.get("error")

    return NormalizedEvent(
        source_file=event.get("file_name") or "",
        log_family=LogFamily.DEBIT_PORTAL,
        event_no=event.get("line_no") or 0,
        physical_line_start=None,  # not tracked by this parser's adapter -- see Phase 2 limitations
        raw_reference=event.get("raw") or "",
        source_event_id=event.get("event_id"),
        batch_id=event.get("batch_id"),
        event_timestamp=event.get("ts_utc"),
        level=level,
        tracker_no=tracker_no,
        tracker_type=tracker_type,
        phase=phase,
        event_type=component,
        transaction_id=transaction_id,
        ds_transaction_id=None,  # Debit Portal is not a 3DS-flow log family
        stepup_request_id=None,
        credential_id=None,
        correlation_id=correlation_id,
        tran_ref=None,
        oob_tracker_id=None,
        msg_id=None,
        issuer_id=tx.get("issuer_id"),
        bank_org=None,
        merchant_name=merchant.get("name"),
        merchant_id=merchant.get("id"),
        amount=transaction_info.get("amount"),
        currency=resolve_currency_code(transaction_info.get("currency")),
        card_last4=extract_card_last4(parsed.get("masked_card")),
        channel=None,
        masked_mobile=mask_mobile(mobile),
        masked_email=mask_email(email),
        customer_id=None,
        authentication_method="OTP" if (tx.get("otp_processed") or parsed.get("otp")) else None,
        credential_type=None,
        verification_token_present=False,
        otp_reference_code=None,
        stepup_status=tx.get("status"),
        oob_status=None,
        card_blocked=None,
        dependency_name=None,
        endpoint=None,
        queue_name=queue if isinstance(queue, str) else None,
        response_code=None,
        http_status=None,
        business_error_code=(error if isinstance(error, str) else None),
        latency_ms=None,
        normalized_stage=classify_normalized_stage(component, level),
        terminal_status=tx.get("integrity_status") or tx.get("status"),
        failure_signature=failure_sig,
        parse_status=parse_status,
        used_fallback_parsing=parsed.get("parse_method") == "regex_xml_fallback",
        correlation_confidence=confidence,
        evidence_level="full" if event.get("raw") else "partial",
        sensitive_fields_removed=sensitive_removed,
    )
