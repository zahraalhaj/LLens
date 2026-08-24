"""
Investigation-focused analysis for the AFS/Netcetera 3DS StepUp log format.

Deliberately downstream of, and read-only against, both the parser's own
output (parser_AFS_Netcetera.py -- untouched) and the stored canonical
events it produces. Nothing here mutates the transaction JSON structure the
parser builds; this module only *reads* the `correlation_id` and
`details.transaction` data already attached to each stored event by the
parser's adapter, and computes new, separate reports from it.

All timestamps handled here are LLens's own normalized `ts_utc` strings
("%Y-%m-%dT%H:%M:%SZ", always UTC -- see core/timezones.py), not the raw
log timestamps, so comparisons are correct regardless of source format.
"""
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.analysis.normalized_schema import (
    LogFamily,
    NormalizedEvent,
    build_failure_signature,
    classify_normalized_stage,
    derive_tracker_type_and_phase,
    looks_like_tracker,
    mask_email,
    mask_mobile,
)

VPLUS_COMPONENTS = {"vplus_input", "vplus_response", "netcetera_response"}

# Matches parser_AFS_Netcetera.py's DEFAULT_SOURCE_SYSTEM -- kept here too
# (not imported from the parser module) so this analysis module has no
# dependency on the custom_parsers package, just a shared convention.
DEFAULT_SOURCE_SYSTEM = "afs_netcetera_3ds_stepup"

DEFAULT_GAP_THRESHOLD_MINUTES = 10  # clustering distance: how close together consecutive unresponded inputs must be to report them as one downtime window, rather than raw log-silence detection
DEFAULT_EXPECTED_RESPONSE_MS = 1000  # a real V+ response should arrive near-instantly
DEFAULT_UNRESPONDED_GRACE_MS = 5000  # wait at least this long past an input's timestamp before concluding it never got a response at all -- protects against flagging a request that's just still in flight (matters most for the live 5-minute polling job)
DEFAULT_EXPECTED_SMS_QUEUE_MS = 30000

_MAX_DETAIL_ROWS = 50  # cap on per-item detail lists returned in a report


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _ms_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    delta_ms = (end - start).total_seconds() * 1000
    return delta_ms if delta_ms >= 0 else None  # negative = out-of-order/clock skew, not a real duration


def extract_merchant_name(event: Dict[str, Any]) -> Optional[str]:
    """The merchant name embedded in this event's transaction snapshot, for
    the by-merchant filter -- same accessor `compute_investigation_summary`
    uses to build `most_affected_merchants`, just exposed for
    pre-aggregation filtering."""
    tx = ((event.get("attributes") or {}).get("details") or {}).get("transaction") or {}
    return (tx.get("merchant") or {}).get("name")


def group_events_by_transaction(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Groups stored events by their resolved correlation_id (the
    transaction ID when the parser resolved one, or the tracker number
    otherwise -- see parser_AFS_Netcetera.py's parse_log_file adapter).
    Assumes events are already sorted ascending by ts_utc (as returned by
    DatabaseManager.get_events_for_analysis)."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in events:
        corr_id = (e.get("attributes") or {}).get("correlation_id")
        if corr_id:
            groups[corr_id].append(e)
    return dict(groups)


# -- 1. V+ availability monitoring -------------------------------------------

def _close_downtime_window(cluster: List[Dict[str, Any]], recovered_at: Optional[str]) -> Dict[str, Any]:
    down_since_ts = cluster[0]["input_ts"]
    recovered_ts = _parse_ts(recovered_at) if recovered_at else None
    return {
        "down_since": cluster[0]["input_time"],
        "recovered_at": recovered_at,
        "duration_minutes": round((recovered_ts - down_since_ts).total_seconds() / 60, 1) if recovered_ts else None,
        "unresponded_count": len(cluster),
        "sample_tracker_no": cluster[0]["tracker_no"],
    }


def compute_vplus_availability(
    events: List[Dict[str, Any]],
    gap_threshold_minutes: int = DEFAULT_GAP_THRESHOLD_MINUTES,
    expected_response_ms: int = DEFAULT_EXPECTED_RESPONSE_MS,
    unresponded_grace_ms: int = DEFAULT_UNRESPONDED_GRACE_MS,
    reference_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Downtime is defined at the request level, not by log silence: every
    vplus_input is expected to receive a matching vplus_response almost
    immediately (within `expected_response_ms`, ~1s). An input that never
    receives ANY response is a concrete failed request -- that's what counts
    as downtime, replacing the old "no V+ log lines for N minutes" gap
    detection, which couldn't tell a genuinely quiet period from a real
    outage (and, worse, compared the log's last timestamp against the
    server's real wall clock -- meaningless for a historical batch upload
    whose data doesn't extend up to the present).

    Two distinct signals are reported, deliberately kept separate:
      - `status == "no_data"`: there is NO V+/StepUp activity of any kind
        (input, response, or netcetera_response) in the window -- nothing
        is being logged at all. Independent of whether any request
        succeeded or failed.
      - `status == "down"`: there IS V+ activity, but at least one
        vplus_input never got a vplus_response, and that failure is still
        unresolved (no later input in the same cluster has since
        succeeded).
    """
    vplus_events = [e for e in events if e.get("component") in VPLUS_COMPONENTS and e.get("ts_utc")]
    if not vplus_events:
        return {
            "status": "no_data",
            "message": "No V+/StepUp activity found in the analyzed window -- cannot determine availability.",
            "expected_response_ms": expected_response_ms,
            "downtime_windows": [],
            "currently_down": None,
        }

    reference_now = reference_now or datetime.now(timezone.utc)
    tx_groups = group_events_by_transaction(vplus_events)

    resolved: List[Dict[str, Any]] = []
    for corr_id, tx_events in tx_groups.items():
        inputs = sorted((e for e in tx_events if e["component"] == "vplus_input"), key=lambda e: e["ts_utc"])
        responses = sorted((e for e in tx_events if e["component"] == "vplus_response"), key=lambda e: e["ts_utc"])

        for inp in inputs:
            input_ts = _parse_ts(inp["ts_utc"])
            if input_ts is None:
                continue
            tracker_no = ((inp.get("attributes") or {}).get("details") or {}).get("tracker_no")
            response_event = next(
                (r for r in responses if _parse_ts(r["ts_utc"]) and _parse_ts(r["ts_utc"]) >= input_ts), None
            )

            if response_event is not None:
                delta_ms = _ms_between(input_ts, _parse_ts(response_event["ts_utc"]))
                resolved.append(
                    {
                        "status": "responded", "correlation_id": corr_id, "tracker_no": tracker_no,
                        "input_ts": input_ts, "input_time": inp["ts_utc"],
                        "response_time_ms": delta_ms, "is_delayed": delta_ms is not None and delta_ms > expected_response_ms,
                    }
                )
                continue

            # No response anywhere in this transaction. Only treat as a
            # confirmed failure once unresponded_grace_ms has elapsed --
            # otherwise a request from moments ago (or one with an
            # apparently-future timestamp due to clock skew, where age_ms
            # comes back None) could just still be in flight.
            age_ms = _ms_between(input_ts, reference_now)
            status = "unresponded" if (age_ms is not None and age_ms >= unresponded_grace_ms) else "pending"
            resolved.append(
                {
                    "status": status, "correlation_id": corr_id, "tracker_no": tracker_no,
                    "input_ts": input_ts, "input_time": inp["ts_utc"],
                    "response_time_ms": None, "is_delayed": False,
                }
            )

    if not resolved:
        # V+ activity exists (e.g. a lone netcetera_response) but there's no
        # vplus_input at all -- nothing to pair, so there's no failed
        # request to report. Distinct from "no_data": there IS activity.
        return {
            "status": "healthy",
            "expected_response_ms": expected_response_ms,
            "gap_threshold_minutes": gap_threshold_minutes,
            "total_inputs_analyzed": 0,
            "responded_count": 0,
            "unresponded_count": 0,
            "unresponded_pct": 0.0,
            "delayed_count": 0,
            "delayed_pct": 0.0,
            "downtime_windows": [],
            "total_downtime_minutes": 0.0,
            "currently_down": False,
            "worst_unresponded": [],
        }

    resolved.sort(key=lambda x: x["input_ts"])
    responded = [r for r in resolved if r["status"] == "responded"]
    unresponded = [r for r in resolved if r["status"] == "unresponded"]
    delayed = [r for r in responded if r["is_delayed"]]

    # Cluster consecutive unresponded inputs (gap under gap_threshold_minutes)
    # into reportable downtime windows -- same "windows" shape the UI already
    # renders, now driven by real request failures instead of raw silence.
    downtime_windows = []
    cluster: List[Dict[str, Any]] = []
    threshold_seconds = gap_threshold_minutes * 60

    for item in resolved:
        if item["status"] != "unresponded":
            if cluster:
                downtime_windows.append(_close_downtime_window(cluster, recovered_at=item["input_time"]))
                cluster = []
            continue
        if cluster and (item["input_ts"] - cluster[-1]["input_ts"]).total_seconds() > threshold_seconds:
            downtime_windows.append(_close_downtime_window(cluster, recovered_at=None))
            cluster = []
        cluster.append(item)
    if cluster:
        downtime_windows.append(_close_downtime_window(cluster, recovered_at=None))

    currently_down = bool(downtime_windows) and downtime_windows[-1]["recovered_at"] is None

    return {
        "status": "down" if currently_down else "healthy",
        "expected_response_ms": expected_response_ms,
        "gap_threshold_minutes": gap_threshold_minutes,
        "window_start": resolved[0]["input_time"],
        "window_end": resolved[-1]["input_time"],
        "total_inputs_analyzed": len(resolved),
        "responded_count": len(responded),
        "unresponded_count": len(unresponded),
        "unresponded_pct": round(100 * len(unresponded) / len(resolved), 1),
        "delayed_count": len(delayed),
        "delayed_pct": round(100 * len(delayed) / len(responded), 1) if responded else 0.0,
        "downtime_windows": downtime_windows,
        "total_downtime_minutes": round(
            sum(w["duration_minutes"] for w in downtime_windows if w["duration_minutes"] is not None), 1
        ),
        "currently_down": currently_down,
        "worst_unresponded": [
            {"correlation_id": u["correlation_id"], "tracker_no": u["tracker_no"], "input_time": u["input_time"]}
            for u in unresponded
        ][:_MAX_DETAIL_ROWS],
    }


# -- 2/3/4. V+ response-time monitoring + delay statistics -------------------

def compute_response_time_stats(
    events: List[Dict[str, Any]],
    expected_response_ms: int = DEFAULT_EXPECTED_RESPONSE_MS,
    include_all_pairs: bool = False,
) -> Dict[str, Any]:
    """Per-transaction V+ round-trip time: vplus_input -> vplus_response.
    Flags any pair exceeding expected_response_ms as delayed, and reports
    aggregate stats (total/avg/min/max/% delayed) -- this single report
    covers both "response-time monitoring" and "delayed responses"
    (they're the same underlying computation at two levels of detail)."""
    tx_groups = group_events_by_transaction(events)
    pairs = []

    for corr_id, tx_events in tx_groups.items():
        inputs = sorted((e for e in tx_events if e["component"] == "vplus_input"), key=lambda e: e["ts_utc"])
        responses = sorted((e for e in tx_events if e["component"] == "vplus_response"), key=lambda e: e["ts_utc"])
        if not inputs or not responses:
            continue

        input_ts = _parse_ts(inputs[0]["ts_utc"])
        response_event = next((r for r in responses if _parse_ts(r["ts_utc"]) and _parse_ts(r["ts_utc"]) >= input_ts), responses[0])
        delta_ms = _ms_between(input_ts, _parse_ts(response_event["ts_utc"]))
        if delta_ms is None:
            continue

        pairs.append(
            {
                "correlation_id": corr_id,
                "input_time": inputs[0]["ts_utc"],
                "response_time": response_event["ts_utc"],
                "response_time_ms": round(delta_ms, 1),
                "is_delayed": delta_ms > expected_response_ms,
                "tracker_no": ((inputs[0].get("attributes") or {}).get("details") or {}).get("tracker_no"),
            }
        )

    if not pairs:
        return {
            "status": "no_data",
            "message": "No complete vplus_input -> vplus_response pairs found in the analyzed window.",
            "expected_response_ms": expected_response_ms,
        }

    values = [p["response_time_ms"] for p in pairs]
    delayed = [p for p in pairs if p["is_delayed"]]

    result = {
        "status": "ok",
        "expected_response_ms": expected_response_ms,
        "total_pairs_analyzed": len(pairs),
        "stats": {
            "total_ms": round(sum(values), 1),
            "avg_ms": round(statistics.mean(values), 1),
            "min_ms": round(min(values), 1),
            "max_ms": round(max(values), 1),
        },
        "delayed_count": len(delayed),
        "delayed_pct": round(100 * len(delayed) / len(pairs), 1),
        "worst_delays": sorted(delayed, key=lambda p: -p["response_time_ms"])[:_MAX_DETAIL_ROWS],
    }
    if include_all_pairs:
        result["all_pairs"] = pairs
    return result


# -- 5. SMS aggregator / flow analysis ----------------------------------------

def compute_sms_analysis(
    events: List[Dict[str, Any]],
    expected_queue_ms: int = DEFAULT_EXPECTED_SMS_QUEUE_MS,
) -> Dict[str, Any]:
    """SMS OTP delivery flow timing and outcome per transaction: how long
    until the SMS was queued, and (with an explicit caveat -- this
    necessarily includes user think-time, it's not a pure system metric)
    how long until the OTP was confirmed. Also reports which transactions
    never reached confirmation (queued but no otp_success = a likely
    delivery failure or user drop-off, not distinguishable from this data
    alone).

    No distinct SMS-aggregator/provider identifier field was found in the
    currently-parsed data -- the source XML doesn't appear to carry one
    (see parser_AFS_Netcetera.py's extract_xml/get_from_xml). This reports
    overall SMS flow instead of a per-aggregator breakdown. If real logs do
    carry a provider field, extraction can be extended via get_from_xml at
    whatever XML path it lives at, and this function's grouping can be
    extended to key by it -- flagged here rather than silently omitted.
    """
    tx_groups = group_events_by_transaction(events)
    results = []

    for corr_id, tx_events in tx_groups.items():
        sms_inputs = sorted((e for e in tx_events if e["component"] == "sms_input"), key=lambda e: e["ts_utc"])
        if not sms_inputs:
            continue
        sms_queues = sorted((e for e in tx_events if e["component"] == "sms_queue"), key=lambda e: e["ts_utc"])
        otp_successes = sorted((e for e in tx_events if e["component"] == "otp_success"), key=lambda e: e["ts_utc"])

        input_ts = _parse_ts(sms_inputs[0]["ts_utc"])
        queue_event = sms_queues[0] if sms_queues else None
        queue_ts = _parse_ts(queue_event["ts_utc"]) if queue_event else None
        queue_delay_ms = _ms_between(input_ts, queue_ts)

        otp_event = otp_successes[0] if otp_successes else None
        completion_ts = _parse_ts(otp_event["ts_utc"]) if otp_event else None
        completion_delay_ms = _ms_between(queue_ts, completion_ts)

        outcome = "otp_confirmed" if otp_event else ("queued_no_confirmation" if queue_event else "queue_failed")

        results.append(
            {
                "correlation_id": corr_id,
                "sms_input_time": sms_inputs[0]["ts_utc"],
                "queued_time": queue_event["ts_utc"] if queue_event else None,
                "queue_delay_ms": round(queue_delay_ms, 1) if queue_delay_ms is not None else None,
                "is_queue_delayed": (queue_delay_ms is not None and queue_delay_ms > expected_queue_ms),
                "otp_confirmed_time": otp_event["ts_utc"] if otp_event else None,
                "completion_delay_ms": round(completion_delay_ms, 1) if completion_delay_ms is not None else None,
                "outcome": outcome,
            }
        )

    if not results:
        return {"status": "no_data", "message": "No SMS OTP activity found in the analyzed window."}

    queue_delays = [r["queue_delay_ms"] for r in results if r["queue_delay_ms"] is not None]
    outcome_counts = Counter(r["outcome"] for r in results)
    unresolved = [r for r in results if r["outcome"] != "otp_confirmed"]

    return {
        "status": "ok",
        "aggregator_note": (
            "No distinct SMS-aggregator identifier field found in the parsed data -- "
            "reporting overall SMS flow rather than a per-aggregator breakdown."
        ),
        "expected_queue_ms": expected_queue_ms,
        "total_sms_transactions": len(results),
        "outcome_counts": dict(outcome_counts),
        "queue_delay_stats": (
            {
                "total_ms": round(sum(queue_delays), 1),
                "avg_ms": round(statistics.mean(queue_delays), 1),
                "min_ms": round(min(queue_delays), 1),
                "max_ms": round(max(queue_delays), 1),
                "delayed_pct": round(100 * sum(1 for r in results if r["is_queue_delayed"]) / len(results), 1),
            }
            if queue_delays
            else None
        ),
        "unresolved": unresolved[:_MAX_DETAIL_ROWS],
    }


# -- 6. Netcetera transaction breakdown (issuer / status) --------------------

def compute_transaction_breakdown(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Issuer and status distribution across every resolved transaction in
    the window -- mirrors parser_AFS_Netcetera.py's own (unused-by-LLens)
    build_summary() bucketing exactly: status is the StepUp response status
    when one was seen, falling back to "OTP_PROCESSED" for transactions
    that completed via the initiate-action/OTP path without an explicit
    StepUp status, else "UNKNOWN". Unlike compute_investigation_summary's
    most_affected_merchants/issuers (which only count ERROR-associated
    transactions), this covers ALL transactions -- success, failure, and
    everything in between."""
    tx_groups = group_events_by_transaction(events)

    issuer_counts: Counter = Counter()
    status_counts: Counter = Counter()
    total_transactions = 0

    for corr_id, tx_events in tx_groups.items():
        tx = next(
            (((e.get("attributes") or {}).get("details") or {}).get("transaction") for e in tx_events
             if ((e.get("attributes") or {}).get("details") or {}).get("transaction")),
            None,
        )
        if not tx:
            continue
        total_transactions += 1

        issuer_counts[tx.get("issuer_id") or "UNKNOWN"] += 1

        status = tx.get("stepup_status")
        if not status and tx.get("otp_processed"):
            status = "OTP_PROCESSED"
        status_counts[status or "UNKNOWN"] += 1

    if total_transactions == 0:
        return {"status": "no_data", "message": "No resolved transactions found in the analyzed window."}

    return {
        "status": "ok",
        "total_transactions": total_transactions,
        "issuer_counts": dict(issuer_counts.most_common()),
        "status_counts": dict(status_counts.most_common()),
    }


# -- 7. Comprehensive investigation-focused correlation -----------------------

def compute_investigation_summary(
    events: List[Dict[str, Any]],
    vplus_report: Dict[str, Any],
    response_time_report: Dict[str, Any],
    sms_report: Dict[str, Any],
) -> Dict[str, Any]:
    """Correlates the other reports plus raw error events into a ranked
    list of findings -- the goal is surfacing what actually matters for an
    investigation, not dumping every number computed above. Reuses the
    transaction context (merchant/issuer/customer) the parser's adapter
    already attached to each event's attributes -- no new extraction, just
    cross-referencing what's already there."""
    error_events = [e for e in events if e.get("level") == "ERROR"]
    error_message_counts = Counter((e.get("message") or "")[:120] for e in error_events)

    merchant_error_counts: Counter = Counter()
    issuer_error_counts: Counter = Counter()
    for e in error_events:
        tx = ((e.get("attributes") or {}).get("details") or {}).get("transaction")
        if tx:
            merchant = (tx.get("merchant") or {}).get("name")
            if merchant:
                merchant_error_counts[merchant] += 1
            issuer = tx.get("issuer_id")
            if issuer:
                issuer_error_counts[issuer] += 1

    findings: List[Dict[str, str]] = []

    for window in vplus_report.get("downtime_windows", [])[:5]:
        if window.get("recovered_at"):
            findings.append(
                {
                    "severity": "high",
                    "finding": f"V+ outage: {window['unresponded_count']} request(s) got no vplus_response, "
                    f"from {window['down_since']} to {window['recovered_at']} ({window['duration_minutes']} min).",
                }
            )
        else:
            findings.append(
                {
                    "severity": "critical",
                    "finding": f"V+ appears DOWN right now -- {window['unresponded_count']} request(s) have "
                    f"gotten no vplus_response since {window['down_since']}.",
                }
            )

    if response_time_report.get("status") == "ok" and response_time_report.get("delayed_pct", 0) >= 10:
        findings.append(
            {
                "severity": "medium",
                "finding": f"{response_time_report['delayed_pct']}% of V+ responses exceeded the "
                f"expected response time ({response_time_report['expected_response_ms']}ms).",
            }
        )

    if sms_report.get("status") == "ok":
        unconfirmed_pct = 100 - (
            100 * sms_report["outcome_counts"].get("otp_confirmed", 0) / sms_report["total_sms_transactions"]
        )
        if unconfirmed_pct >= 20:
            findings.append(
                {
                    "severity": "medium",
                    "finding": f"{round(unconfirmed_pct, 1)}% of SMS OTP flows never reached confirmation "
                    f"(queued_no_confirmation or queue_failed).",
                }
            )

    for merchant, count in merchant_error_counts.most_common(5):
        findings.append({"severity": "medium", "finding": f"Merchant '{merchant}' associated with {count} error event(s)."})

    for msg, count in error_message_counts.most_common(5):
        if count > 1:
            findings.append({"severity": "low", "finding": f"Recurring error ({count}x): {msg}"})

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: severity_order.get(f["severity"], 9))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events_analyzed": len(events),
        "total_errors": len(error_events),
        "top_findings": findings[:20],
        "error_message_frequency": dict(error_message_counts.most_common(10)),
        "most_affected_merchants": dict(merchant_error_counts.most_common(10)),
        "most_affected_issuers": dict(issuer_error_counts.most_common(10)),
    }


# -- 8. Normalization (Phase 2 of the multi-log analysis strategy) -----------

def normalize_netcetera_event(event: Dict[str, Any]) -> NormalizedEvent:
    """Maps one stored AFS/Netcetera CanonicalLogEvent into the common
    NormalizedEvent shape (see backend/analysis/normalized_schema.py).

    LIMITATION: parser_AFS_Netcetera.py's parse_log_file() adapter attaches
    a deliberately compact `details.transaction` snapshot to each event (by
    its own docstring: "not the transaction's full nested event list, to
    avoid duplicating every sibling event's data") -- payment/card data and
    the initiate_action-side stepup_request_id/sms_msg_id are NOT included
    in that snapshot, only in the parser's internal, non-persisted
    tx_context. So card_last4, stepup_request_id, and oob_tracker_id are
    unavailable at this layer for this family; they read as None here, not
    as parsing failures. See the Phase 2 report for the full note."""
    attrs = event.get("attributes") or {}
    details = attrs.get("details") or {}
    tx = details.get("transaction") or {}
    correlation_id = attrs.get("correlation_id")

    tracker_no = details.get("tracker_no")
    if not tracker_no and looks_like_tracker(correlation_id):
        tracker_no = correlation_id
    tracker_type = details.get("tracker_type")
    derived_type, derived_phase = derive_tracker_type_and_phase(tracker_no)
    tracker_type = tracker_type or derived_type

    transaction_id = tx.get("transaction_id")
    derived = tx.get("derived") or {}
    customer = tx.get("customer") or {}
    merchant = tx.get("merchant") or {}
    transaction_info = tx.get("transaction_info") or {}
    stepup_status = tx.get("stepup_status")

    sensitive_removed: List[str] = []
    if customer.get("mobile"):
        sensitive_removed.append("customer.mobile")
    if customer.get("email"):
        sensitive_removed.append("customer.email")

    level = event.get("level")
    component = event.get("component")
    is_unparsed = component == "unparsed"
    failure_sig = None
    if level in ("ERROR", "CRITICAL") or is_unparsed:
        reason = event.get("message") or "unparsed_event"
        failure_sig = build_failure_signature(LogFamily.NETCETERA_VPLUS, component, reason)

    confidence = 1.0 if transaction_id else (0.5 if tracker_no else 0.2)

    authentication_method = "STEPUP" if derived.get("has_stepup") else ("OTP" if derived.get("has_initiate_action") else None)
    channel = "SMS" if derived.get("has_sms") else ("EMAIL" if derived.get("has_email") else None)

    # The only case a true original file line number survives to this
    # layer: failed-to-parse lines, which the adapter records verbatim
    # (see parser_AFS_Netcetera.py's parse_log_file, the `for failed in
    # failed_lines` branch) rather than dropping.
    physical_line_start = details.get("line_no") if is_unparsed and isinstance(details.get("line_no"), int) else None

    return NormalizedEvent(
        source_file=event.get("file_name") or "",
        log_family=LogFamily.NETCETERA_VPLUS,
        event_no=event.get("line_no") or 0,
        physical_line_start=physical_line_start,
        raw_reference=event.get("raw") or "",
        source_event_id=event.get("event_id"),
        batch_id=event.get("batch_id"),
        event_timestamp=event.get("ts_utc"),
        level=level,
        tracker_no=tracker_no,
        tracker_type=tracker_type,
        phase=derived_phase,
        event_type=component,
        transaction_id=transaction_id,
        ds_transaction_id=transaction_id,  # same rationale as Cardinal: this family's TransactionId is the shared 3DS DS transaction id
        stepup_request_id=None,  # not retained in this family's per-event details snapshot -- see docstring above
        credential_id=None,
        correlation_id=correlation_id,
        tran_ref=None,
        oob_tracker_id=None,
        msg_id=details.get("msg_id"),
        issuer_id=tx.get("issuer_id"),
        bank_org=None,
        merchant_name=merchant.get("name"),
        merchant_id=merchant.get("id"),
        amount=transaction_info.get("amount"),
        currency=transaction_info.get("currency"),
        card_last4=None,  # not exposed at this layer -- see docstring above
        channel=channel,
        masked_mobile=mask_mobile(customer.get("mobile")),
        masked_email=mask_email(customer.get("email")),
        customer_id=None,
        authentication_method=authentication_method,
        credential_type=None,
        verification_token_present=False,
        otp_reference_code=None,
        stepup_status=stepup_status,
        oob_status=None,
        card_blocked=None,
        dependency_name=None,
        endpoint=None,
        queue_name=None,
        response_code=None,
        http_status=None,
        business_error_code=None,
        latency_ms=None,  # this family's own compute_response_time_stats() already owns per-request latency; not duplicated here
        normalized_stage=classify_normalized_stage(component, level),
        terminal_status=("SUCCESS" if derived.get("is_success") else stepup_status),
        failure_signature=failure_sig,
        parse_status="failed" if is_unparsed else "parsed",
        correlation_confidence=confidence,
        evidence_level="full" if event.get("raw") else "minimal",
        sensitive_fields_removed=sensitive_removed,
    )
