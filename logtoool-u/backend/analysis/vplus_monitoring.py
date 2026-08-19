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

VPLUS_COMPONENTS = {"vplus_input", "vplus_response", "netcetera_response"}

# Matches parser_AFS_Netcetera.py's DEFAULT_SOURCE_SYSTEM -- kept here too
# (not imported from the parser module) so this analysis module has no
# dependency on the custom_parsers package, just a shared convention.
DEFAULT_SOURCE_SYSTEM = "afs_netcetera_3ds_stepup"

DEFAULT_GAP_THRESHOLD_MINUTES = 10
DEFAULT_EXPECTED_RESPONSE_MS = 5000
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

def compute_vplus_availability(
    events: List[Dict[str, Any]],
    gap_threshold_minutes: int = DEFAULT_GAP_THRESHOLD_MINUTES,
    reference_now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Detects gaps in V+/StepUp/Netcetera activity longer than the
    threshold -- each gap is reported as a downtime window with exactly
    when it started (last event seen before the gap) and when service
    returned to normal (first event seen after it). Also reports whether
    V+ appears down *right now* (no activity since before the threshold,
    relative to `reference_now`)."""
    vplus_events = [e for e in events if e.get("component") in VPLUS_COMPONENTS and e.get("ts_utc")]
    vplus_events.sort(key=lambda e: e["ts_utc"])

    if not vplus_events:
        return {
            "status": "no_data",
            "message": "No V+/StepUp activity found in the analyzed window -- cannot determine availability.",
            "downtime_windows": [],
            "currently_down": None,
            "gap_threshold_minutes": gap_threshold_minutes,
        }

    threshold_seconds = gap_threshold_minutes * 60
    downtime_windows = []

    for i in range(1, len(vplus_events)):
        prev_ts = _parse_ts(vplus_events[i - 1]["ts_utc"])
        curr_ts = _parse_ts(vplus_events[i]["ts_utc"])
        if not prev_ts or not curr_ts:
            continue
        gap_seconds = (curr_ts - prev_ts).total_seconds()
        if gap_seconds > threshold_seconds:
            downtime_windows.append(
                {
                    "down_since": vplus_events[i - 1]["ts_utc"],
                    "recovered_at": vplus_events[i]["ts_utc"],
                    "duration_minutes": round(gap_seconds / 60, 1),
                    "last_event_before_down": {
                        "component": vplus_events[i - 1]["component"],
                        "correlation_id": (vplus_events[i - 1].get("attributes") or {}).get("correlation_id"),
                    },
                    "first_event_after_recovery": {
                        "component": vplus_events[i]["component"],
                        "correlation_id": (vplus_events[i].get("attributes") or {}).get("correlation_id"),
                    },
                }
            )

    reference_now = reference_now or datetime.now(timezone.utc)
    last_event_ts = _parse_ts(vplus_events[-1]["ts_utc"])
    minutes_since_last = (reference_now - last_event_ts).total_seconds() / 60 if last_event_ts else None
    # Clamp negative values (the "last" event's timestamp is after our
    # reference clock) to 0 rather than showing a confusing negative
    # "ago" figure -- this happens with real clock skew between a log
    # source and the server, not just malformed data, so it's worth
    # handling gracefully rather than treating as an error.
    if minutes_since_last is not None and minutes_since_last < 0:
        minutes_since_last = 0.0
    currently_down = minutes_since_last is not None and minutes_since_last > gap_threshold_minutes

    return {
        "status": "down" if currently_down else "healthy",
        "gap_threshold_minutes": gap_threshold_minutes,
        "total_events_analyzed": len(vplus_events),
        "window_start": vplus_events[0]["ts_utc"],
        "window_end": vplus_events[-1]["ts_utc"],
        "downtime_windows": downtime_windows,
        "total_downtime_minutes": round(sum(w["duration_minutes"] for w in downtime_windows), 1),
        "currently_down": currently_down,
        "minutes_since_last_event": round(minutes_since_last, 1) if minutes_since_last is not None else None,
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


# -- 6. Comprehensive investigation-focused correlation -----------------------

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

    if vplus_report.get("currently_down"):
        findings.append(
            {
                "severity": "critical",
                "finding": f"V+ appears DOWN right now -- no activity for "
                f"{vplus_report.get('minutes_since_last_event')} minutes.",
            }
        )

    for window in vplus_report.get("downtime_windows", [])[:5]:
        findings.append(
            {
                "severity": "high",
                "finding": f"V+ outage: down from {window['down_since']} to {window['recovered_at']} "
                f"({window['duration_minutes']} min).",
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
