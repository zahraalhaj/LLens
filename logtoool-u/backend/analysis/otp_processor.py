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


def normalize_otp_event(event: Dict[str, Any]) -> NormalizedEvent:
    """Maps one stored OTP Online Processor CanonicalLogEvent into the
    common NormalizedEvent shape (see backend/analysis/normalized_schema.py).
    Reads `attributes.details.record` -- the tracker's final merged record
    parser_OTP_Processor.py's adapter attaches, which carries a raw `otp`
    value, a raw `otppan`, and a raw `mobile`/`email` -- none of which are
    copied forward. `otp_reference_code` is populated from the SMS queue's
    own message id (`sms_msg_id`), a legitimate non-sensitive reference,
    never from the OTP value itself."""
    attrs = event.get("attributes") or {}
    details = attrs.get("details") or {}
    record = details.get("record") or {}
    per_event_parsed = details.get("parsed") or {}
    correlation_id = attrs.get("correlation_id")

    tracker_no = details.get("tracker_no") or record.get("tracker_no")
    if not tracker_no and looks_like_tracker(correlation_id):
        tracker_no = correlation_id
    tracker_type, phase = derive_tracker_type_and_phase(tracker_no)

    merchant_details = record.get("merchant_details") or {}
    if not isinstance(merchant_details, dict):
        merchant_details = {}
    transaction_info = record.get("transaction") or {}
    if not isinstance(transaction_info, dict):
        transaction_info = {}

    mobile = record.get("mobile")
    email = record.get("email")

    sensitive_removed: List[str] = []
    if mobile:
        sensitive_removed.append("record.mobile")
    if email:
        sensitive_removed.append("record.email")
    if record.get("otp"):
        sensitive_removed.append("record.otp")
    if record.get("otppan"):
        sensitive_removed.append("record.otppan")

    level = event.get("level")
    component = event.get("component")
    parse_error = details.get("parse_error")
    failure_sig = None
    if level in ("ERROR", "CRITICAL") or parse_error or component == "other":
        reason = parse_error or event.get("message") or "unclassified_event"
        failure_sig = build_failure_signature(LogFamily.OTP_PROCESSOR, component, reason)

    # No transaction/stepup-level identifier exists in this family -- the
    # tracker number is the strongest identifier available, unlike
    # Cardinal/VFlex/Netcetera which can resolve a transaction_id.
    confidence = 0.5 if tracker_no else 0.2
    channel = "EMAIL" if email else ("SMS" if mobile else None)

    return NormalizedEvent(
        source_file=event.get("file_name") or "",
        log_family=LogFamily.OTP_PROCESSOR,
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
        transaction_id=None,
        ds_transaction_id=None,
        stepup_request_id=None,
        credential_id=None,
        correlation_id=correlation_id,
        tran_ref=None,
        oob_tracker_id=None,
        msg_id=record.get("sms_msg_id"),
        issuer_id=None,
        bank_org=record.get("org"),
        merchant_name=record.get("merchant"),
        merchant_id=merchant_details.get("id"),
        amount=transaction_info.get("amount"),
        currency=transaction_info.get("currency"),
        card_last4=extract_card_last4(record.get("masked_card")),
        channel=channel,
        masked_mobile=mask_mobile(mobile),
        masked_email=mask_email(email),
        customer_id=None,
        authentication_method="OTP",
        credential_type=channel,
        verification_token_present=False,
        otp_reference_code=record.get("sms_msg_id"),
        stepup_status=None,
        oob_status=None,
        card_blocked=None,
        dependency_name="otp_processor_queue" if record.get("queue") else None,
        endpoint=None,
        queue_name=record.get("queue") if isinstance(record.get("queue"), str) else None,
        response_code=None,
        http_status=None,
        business_error_code=None,
        latency_ms=None,
        normalized_stage=classify_normalized_stage(component, level),
        terminal_status=("PROCESSED" if record.get("otp_processed") else None),
        failure_signature=failure_sig,
        parse_status="failed" if parse_error else "parsed",
        used_fallback_parsing=(record.get("parse_method") or per_event_parsed.get("parse_method")) == "regex_fallback",
        correlation_confidence=confidence,
        evidence_level="full" if event.get("raw") else "partial",
        sensitive_fields_removed=sensitive_removed,
    )
