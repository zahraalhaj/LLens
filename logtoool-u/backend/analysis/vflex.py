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

DEFAULT_SOURCE_SYSTEM = "vflex_transaction_log"

_MAX_FAILED_ITEMS = 100
_TOP_MERCHANTS_LIMIT = 15
_TOP_FAILURE_REASONS_LIMIT = 10


def extract_merchant_name(event: Dict[str, Any]) -> Optional[str]:
    """The merchant name embedded in this event's transaction snapshot, for
    the by-merchant filter -- same accessor `compute_vflex_summary` uses to
    build `top_merchants`, just exposed for pre-aggregation filtering."""
    tx = ((event.get("attributes") or {}).get("details") or {}).get("transaction") or {}
    return (tx.get("merchant") or {}).get("name")


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
    by_currency: Counter = Counter()
    by_merchant: Counter = Counter()
    otp_processed_count = 0
    bank_api_success_count = 0
    checks_needed = 0

    for tx in transactions.values():
        by_issuer[tx.get("issuer_id") or "UNKNOWN"] += 1
        by_status[tx.get("status") or tx.get("integrity_status") or "UNKNOWN"] += 1
        by_bank_operation[(tx.get("bank_api") or {}).get("operation") or "UNKNOWN"] += 1
        by_channel[(tx.get("otp") or {}).get("channel") or "UNKNOWN"] += 1
        by_currency[resolve_currency_code((tx.get("transaction") or {}).get("currency")) or "UNKNOWN"] += 1
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
        "by_currency": dict(by_currency.most_common()),
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


def normalize_vflex_event(event: Dict[str, Any]) -> NormalizedEvent:
    """Maps one stored VFlex CanonicalLogEvent into the common
    NormalizedEvent shape (see backend/analysis/normalized_schema.py).
    Reads `attributes.details.transaction` -- the resolved tracker-record
    snapshot parser_VFlex.py's adapter already attaches, including the
    already-masked `payment.masked_card`/`payment.last4_pan` fields and the
    raw `otp.value`/`otp.sms_message_decoded` fields that must never be
    copied forward."""
    attrs = event.get("attributes") or {}
    details = attrs.get("details") or {}
    tx = details.get("transaction") or {}
    correlation_id = attrs.get("correlation_id")

    tracker_no = tx.get("tracker_no")
    if not tracker_no and looks_like_tracker(correlation_id):
        tracker_no = correlation_id
    tracker_type = tx.get("tracker_type")
    phase = tx.get("phase")
    derived_type, derived_phase = derive_tracker_type_and_phase(tracker_no)
    tracker_type = tracker_type or derived_type
    phase = phase or derived_phase

    customer = tx.get("customer") or {}
    merchant = tx.get("merchant") or {}
    transaction_info = tx.get("transaction") or {}
    payment = tx.get("payment") or {}
    bank_api = tx.get("bank_api") or {}
    otp = tx.get("otp") or {}
    queue = tx.get("queue") or {}
    error = tx.get("error") or {}

    sensitive_removed: List[str] = []
    if customer.get("mobile"):
        sensitive_removed.append("customer.mobile")
    if customer.get("email"):
        sensitive_removed.append("customer.email")
    if otp.get("value"):
        sensitive_removed.append("otp.value")
    if otp.get("sms_message_decoded"):
        sensitive_removed.append("otp.sms_message_decoded")
    if otp.get("sms_message_base64"):
        sensitive_removed.append("otp.sms_message_base64")
    if tx.get("verification_token"):
        sensitive_removed.append("verification_token")

    level = event.get("level")
    component = event.get("component")
    parse_status = details.get("parse_status") or "parsed"
    warnings = details.get("warnings") or tx.get("warnings") or []
    # parser_VFlex.py appends this exact literal warning when a JSON
    # payload couldn't be parsed cleanly and had to go through
    # fallback_json_fields() instead of normal JSON parsing.
    used_fallback_parsing = any("fallback parsing" in w for w in warnings if w)
    failure_sig = None
    if level in ("ERROR", "CRITICAL") or parse_status == "partial":
        reason = (warnings[0] if warnings else None) or error.get("description") or event.get("message") or "unknown_error"
        failure_sig = build_failure_signature(LogFamily.VFLEX, component, reason)

    transaction_id = tx.get("transaction_id")
    stepup_request_id = tx.get("stepup_request_id")
    confidence = 1.0 if transaction_id else (0.7 if stepup_request_id else (0.5 if tracker_no else 0.2))

    return NormalizedEvent(
        source_file=event.get("file_name") or "",
        log_family=LogFamily.VFLEX,
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
        ds_transaction_id=None,  # VFlex is not a 3DS Directory Server log family
        stepup_request_id=stepup_request_id,
        credential_id=None,
        correlation_id=correlation_id,
        tran_ref=bank_api.get("transaction_reference"),
        oob_tracker_id=None,
        msg_id=queue.get("message_id"),
        issuer_id=tx.get("issuer_id"),
        bank_org=None,
        merchant_name=merchant.get("name"),
        merchant_id=None,
        amount=transaction_info.get("amount"),
        currency=resolve_currency_code(transaction_info.get("currency")),
        card_last4=extract_card_last4(payment.get("last4_pan"), payment.get("masked_card")),
        channel=otp.get("channel"),
        masked_mobile=mask_mobile(customer.get("mobile")),
        masked_email=mask_email(customer.get("email")),
        customer_id=customer.get("client_customer_id"),
        authentication_method=(tx.get("stepup_type") or ("OTP" if otp.get("channel") else None)),
        credential_type=otp.get("channel"),
        verification_token_present=bool(tx.get("verification_token")),
        otp_reference_code=None,  # VFlex surfaces no distinct reference id -- only the raw OTP value, which must never be stored
        stepup_status=tx.get("status"),
        oob_status=None,
        card_blocked=None,
        dependency_name="bank_api" if (bank_api.get("url") or bank_api.get("operation")) else None,
        endpoint=bank_api.get("url"),
        queue_name=queue.get("name"),
        response_code=bank_api.get("org_number"),
        http_status=None,
        business_error_code=error.get("reference_number"),
        latency_ms=None,
        normalized_stage=classify_normalized_stage(component, level),
        terminal_status=tx.get("integrity_status") or tx.get("status"),
        failure_signature=failure_sig,
        parse_status=parse_status,
        used_fallback_parsing=used_fallback_parsing,
        correlation_confidence=confidence,
        evidence_level="full" if event.get("raw") else "partial",
        sensitive_fields_removed=sensitive_removed,
    )
