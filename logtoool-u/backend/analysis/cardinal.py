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

DEFAULT_SOURCE_SYSTEM = "cardinal_stepup_oob_log"

_MAX_FAILED_ITEMS = 100
_TOP_MERCHANTS_LIMIT = 15
_TOP_FAILURE_REASONS_LIMIT = 10


def extract_merchant_name(event: Dict[str, Any]) -> Optional[str]:
    """The merchant name embedded in this event's flow snapshot, for the
    by-merchant filter -- same accessor `compute_cardinal_summary` uses to
    build `top_merchants`, just exposed for pre-aggregation filtering."""
    flow = ((event.get("attributes") or {}).get("details") or {}).get("flow") or {}
    return (flow.get("merchant") or {}).get("name")


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


def normalize_cardinal_event(event: Dict[str, Any]) -> NormalizedEvent:
    """Maps one stored Cardinal CanonicalLogEvent into the common
    NormalizedEvent shape (see backend/analysis/normalized_schema.py).
    Deliberately reads only `attributes.details`/`attributes.correlation_id`
    -- the flow snapshot and per-event normalized_payloads parser_Cardinal.py's
    adapter already resolved -- and never re-parses `raw`.

    Cardinal's flow is the richest of the five families (it carries a full
    `payment` block with a RAW card number and a `verification_token`) --
    both are deliberately left out of every field below except as a
    derived last-4 digit / boolean-presence flag."""
    attrs = event.get("attributes") or {}
    details = attrs.get("details") or {}
    flow = details.get("flow") or {}
    identifiers = details.get("identifiers") or {}
    payloads = details.get("normalized_payloads") or []
    first_payload = payloads[0] if payloads and isinstance(payloads[0], dict) else {}

    correlation_id = attrs.get("correlation_id")
    trackers = flow.get("trackers") or []
    tracker_no = (trackers[0] if trackers else None) or identifiers.get("oob_tracker_id")
    if not tracker_no and looks_like_tracker(correlation_id):
        tracker_no = correlation_id
    derived_type, derived_phase = derive_tracker_type_and_phase(tracker_no)
    tracker_type = derived_type
    phase = details.get("phase") or derived_phase

    transaction_id = flow.get("transaction_id") or first_payload.get("transaction_id")
    stepup_request_ids = flow.get("stepup_request_ids") or identifiers.get("stepup_request_ids") or []
    stepup_request_id = (stepup_request_ids[0] if stepup_request_ids else None) or first_payload.get("stepup_request_id")

    auth = flow.get("authentication") or {}
    payment = flow.get("payment") or first_payload.get("payment") or {}
    customer = flow.get("customer") or first_payload.get("customer") or {}
    merchant = flow.get("merchant") or first_payload.get("merchant") or {}
    transaction = flow.get("transaction") or first_payload.get("transaction") or {}
    oob = flow.get("oob") or {}
    payload_credentials = first_payload.get("credentials") or {}
    credential_items = payload_credentials.get("items") or []
    first_credential = credential_items[0] if credential_items and isinstance(credential_items[0], dict) else {}
    credential_id = payload_credentials.get("oob_credential_id") or first_credential.get("id")
    credential_type = first_credential.get("type")

    sensitive_removed: List[str] = []
    if payment.get("card_number"):
        sensitive_removed.append("payment.card_number")
    if customer.get("mobile"):
        sensitive_removed.append("customer.mobile")
    if customer.get("email"):
        sensitive_removed.append("customer.email")
    if auth.get("verification_token"):
        sensitive_removed.append("authentication.verification_token")

    level = event.get("level")
    component = event.get("component")
    parse_status = details.get("parse_status") or "parsed"
    warnings = details.get("warnings") or flow.get("warnings") or []
    # parser_Cardinal.py appends this exact literal warning when a JSON
    # payload couldn't be parsed cleanly and had to go through
    # regex_json_fallback() instead of normal JSON parsing.
    used_fallback_parsing = any("fallback parsing" in w for w in warnings if w)
    failure_sig = None
    if level in ("ERROR", "CRITICAL") or parse_status == "partial":
        reason = (warnings[0] if warnings else None) or event.get("message") or "unknown_error"
        failure_sig = build_failure_signature(LogFamily.CARDINAL, component, reason)

    confidence = 1.0 if transaction_id else (0.7 if stepup_request_id else (0.5 if tracker_no else 0.2))

    oob_status_history = oob.get("status_history") or []
    card_blocked_history = oob.get("card_blocked_history") or []
    http = details.get("http") or {}
    queue = details.get("queue") or {}
    vplus = details.get("vplus") or {}

    return NormalizedEvent(
        source_file=event.get("file_name") or "",
        log_family=LogFamily.CARDINAL,
        event_no=event.get("line_no") or 0,
        physical_line_start=None,  # parser_Cardinal.py computes this internally but its adapter doesn't propagate it into `details` -- see Phase 2 limitations
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
        ds_transaction_id=transaction_id,  # Cardinal's TransactionId is the 3DS Directory Server id shared with the Netcetera/V+ leg of the same flow
        stepup_request_id=stepup_request_id,
        credential_id=credential_id,
        correlation_id=correlation_id,
        tran_ref=None,
        oob_tracker_id=identifiers.get("oob_tracker_id") or (oob.get("oob_tracker_ids") or [None])[0],
        msg_id=queue.get("message_id"),
        issuer_id=flow.get("issuer_id") or first_payload.get("issuer_id"),
        bank_org=flow.get("bank_org"),
        merchant_name=merchant.get("name"),
        merchant_id=merchant.get("id"),
        amount=transaction.get("amount"),
        currency=transaction.get("currency"),
        card_last4=extract_card_last4(payment.get("card_number")),
        channel=None,
        masked_mobile=mask_mobile(customer.get("mobile")),
        masked_email=mask_email(customer.get("email")),
        customer_id=flow.get("customer_id") or customer.get("customer_id"),
        authentication_method=auth.get("type"),
        credential_type=credential_type,
        verification_token_present=bool(auth.get("verification_token")),
        otp_reference_code=auth.get("otp_reference_code"),
        stepup_status=auth.get("status"),
        oob_status=(oob_status_history[-1] if oob_status_history else None),
        card_blocked=(bool(card_blocked_history[-1]) if card_blocked_history else None),
        dependency_name="cardinal_oob_api" if http.get("urls") else None,
        endpoint=(http.get("urls") or [None])[0],
        queue_name=(queue.get("names") or [None])[0],
        response_code=vplus.get("response_code"),
        http_status=http.get("status_code"),
        business_error_code=http.get("error_code"),
        latency_ms=None,  # per-event request/response pairing isn't resolved at this layer -- see limitations
        normalized_stage=classify_normalized_stage(component, level),
        terminal_status=flow.get("integrity_status"),
        failure_signature=failure_sig,
        parse_status=parse_status,
        used_fallback_parsing=used_fallback_parsing,
        correlation_confidence=confidence,
        evidence_level="full" if (event.get("raw") and event.get("ts_utc")) else "partial",
        sensitive_fields_removed=sensitive_removed,
    )
