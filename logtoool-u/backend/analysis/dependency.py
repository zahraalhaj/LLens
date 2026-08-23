"""
Deterministic Operational & Dependency Analysis -- Phase 5 of the LLens
Multi-Log Analysis Strategy.

Operates directly on NormalizedEvents (Phase 2) -- system-wide dependency
health is not scoped to one correlated flow, so this deliberately does NOT
require Phase 3 flow correlation as an input, matching the existing
precedent in backend/analysis/vplus_monitoring.py (compute_response_time_stats
already pairs events directly, independent of any flow model).

No LLM anywhere in this module. Every metric is a deterministic count,
pairing, or statistic over already-normalized fields.

DEPENDENCY CONFIGURATION RATIONALE
-----------------------------------------------------------------------
Every event_type referenced below is a real classify_event() value,
verified against the parser source in Phase 4's stage-mapping work (see
backend/analysis/lifecycle.py's module docstring). A dependency's
"request"/"success"/"error"/"timeout" type sets are deliberately narrow --
only markers with a clear, defensible meaning for THAT specific dependency
are included; a generic "error"/"message" catch-all is never assumed to
belong to a specific dependency unless the parser gives no more specific
error marker (see POSTILION and BANK_API below), and even then it's only
used as a fallback, not a guess.

Database/SQL (VFlex's "sql_connection_success") has NO request-boundary
marker in the current parser vocabulary -- only a success confirmation
exists, with no "connection attempt started" counterpart. Latency cannot
be computed for this dependency without fabricating a request time; see
_pair_dependency_events()'s early-exit branch and the `note` field on its
DependencyMetrics.
"""
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from backend.analysis.dependency_schema import (
    Dependency,
    DependencyMetrics,
    DependencyTimeSeriesReport,
    HandoffStage,
    PairOutcome,
    QueueHandoffReport,
    RequestResponsePair,
    TimeBucketMetrics,
    TrackerHandoffRecord,
    TransitionLatency,
)
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent

# ---------------------------------------------------------------------------
# Per-dependency event-type configuration
# ---------------------------------------------------------------------------


class _DependencyConfig:
    def __init__(
        self,
        families: Tuple[str, ...],
        request_types: Tuple[str, ...],
        success_types: Tuple[str, ...],
        error_types: Tuple[str, ...] = (),
        timeout_types: Tuple[str, ...] = (),
    ):
        self.families = families
        self.request_types = set(request_types)
        self.success_types = set(success_types)
        self.error_types = set(error_types)
        self.timeout_types = set(timeout_types)


DEPENDENCY_CONFIG: Dict[Dependency, _DependencyConfig] = {
    # V+: the same "StepupCall V+ Input/Response Message" wire event is
    # classified independently by both Cardinal and Netcetera parsers (see
    # lifecycle.py's module docstring) -- pairing across both families'
    # events by tracker_no covers whichever side's log is present.
    Dependency.V_PLUS: _DependencyConfig(
        families=(LogFamily.CARDINAL.value, LogFamily.NETCETERA_VPLUS.value),
        request_types=("vplus_input",),
        success_types=("vplus_response",),
        error_types=("vplus_mq_timeout", "web_exception"),
        timeout_types=("vplus_mq_timeout",),
    ),
    # Postilion: the card-switch platform Debit Portal transactions route
    # through. No Postilion-specific request/response marker exists
    # separate from the portal's own debit_request/debit_response pair --
    # that pair IS the Postilion round trip from this log's perspective.
    Dependency.POSTILION: _DependencyConfig(
        families=(LogFamily.DEBIT_PORTAL.value,),
        request_types=("debit_request_json",),
        success_types=("debit_response_json",),
        error_types=("error",),  # no Postilion-specific error marker exists; the family's generic one is the only signal available
        timeout_types=(),
    ),
    Dependency.BANK_API: _DependencyConfig(
        families=(LogFamily.VFLEX.value,),
        request_types=("bank_request",),
        success_types=("bank_api_success_response",),
        error_types=("bank_api_error_response",),
        timeout_types=(),
    ),
    # OOB API: oob_status_poll is an intermediate WAITING signal (see
    # Phase 4's CUSTOMER_RESPONSE_PENDING mapping), not a request needing
    # its own response, so it's deliberately excluded here.
    Dependency.OOB_API: _DependencyConfig(
        families=(LogFamily.CARDINAL.value,),
        request_types=("oob_authenticate_api",),
        success_types=("oob_validate_api",),
        error_types=("oob_http_error", "oob_validate_exception", "oob_empty_status_response", "oob_status_api_error"),
        timeout_types=(),
    ),
    Dependency.OTP_ONLINE_PROCESSOR: _DependencyConfig(
        families=(LogFamily.OTP_PROCESSOR.value,),
        request_types=("msg_received_sms_xml",),
        success_types=("otp_success",),
        error_types=(),  # no explicit error event_type in this family -- see failure_signature fallback in _is_error_event()
        timeout_types=(),
    ),
    # Database/SQL has no request-boundary marker at all (see module
    # docstring) -- handled by the early-exit branch in
    # _pair_dependency_events(), this config exists only for the
    # success_types lookup.
    Dependency.DATABASE_SQL: _DependencyConfig(
        families=(LogFamily.VFLEX.value,),
        request_types=(),
        success_types=("sql_connection_success",),
        error_types=(),
        timeout_types=(),
    ),
}

DEFAULT_EXPECTED_LATENCY_MS: Dict[Dependency, int] = {
    Dependency.V_PLUS: 1000,  # matches vplus_monitoring.py's own DEFAULT_EXPECTED_RESPONSE_MS convention
    Dependency.POSTILION: 3000,
    Dependency.BANK_API: 2000,
    Dependency.OOB_API: 5000,
    Dependency.OTP_ONLINE_PROCESSOR: 30000,  # matches vplus_monitoring.py's DEFAULT_EXPECTED_SMS_QUEUE_MS convention
    Dependency.DATABASE_SQL: 500,
}


def _sort_key(event: NormalizedEvent) -> Tuple[str, int]:
    return (event.event_timestamp or "", event.event_no)


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _join_key(event: NormalizedEvent) -> Optional[str]:
    """tracker_no is the strongest available join key for pairing
    request/response events; correlation_id is the fallback for events
    whose tracker never resolved (still an exact-identifier join -- never
    timestamp-only, per the Phase 3 rule this module also follows)."""
    return event.tracker_no or event.correlation_id


def _is_error_event(event: NormalizedEvent, config: _DependencyConfig) -> bool:
    if event.event_type in config.error_types:
        return True
    # Fallback for families with no dependency-specific error marker (e.g.
    # OTP Online Processor, where an unclassified "other" event is logged
    # at WARN, not ERROR/CRITICAL -- see parser_OTP_Processor.py's adapter).
    # Phase 2 already decided this is a genuine failure before setting
    # failure_signature (ERROR/CRITICAL level OR partial/failed parse
    # status); no additional level check is needed or correct here.
    return bool(event.failure_signature)


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile, stdlib only. pct in [0, 100]."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    rank = max(1, math.ceil((pct / 100.0) * len(ordered)))
    return round(ordered[min(rank, len(ordered)) - 1], 1)


def _pair_dependency_events(events: List[NormalizedEvent], dependency: Dependency) -> List[RequestResponsePair]:
    config = DEPENDENCY_CONFIG[dependency]

    if not config.request_types:
        # No request-boundary marker exists (Database/SQL) -- every
        # success-type event is reported as a standalone confirmation, not
        # a paired response. No latency is ever fabricated for these.
        pairs = []
        for event in events:
            if event.log_family.value in config.families and event.event_type in config.success_types:
                pairs.append(
                    RequestResponsePair(
                        response_event_id=event.source_event_id,
                        join_key=_join_key(event),
                        log_family=event.log_family.value,
                        response_timestamp=event.event_timestamp,
                        outcome=PairOutcome.SUCCESS,
                        event_type=event.event_type,
                    )
                )
        return pairs

    relevant = [e for e in events if e.log_family.value in config.families]
    grouped: Dict[str, List[NormalizedEvent]] = defaultdict(list)
    for event in relevant:
        key = _join_key(event)
        if key:
            grouped[key].append(event)

    pairs: List[RequestResponsePair] = []
    for key, group_events in grouped.items():
        group_sorted = sorted(group_events, key=_sort_key)
        pending_request: Optional[NormalizedEvent] = None
        for event in group_sorted:
            is_request = event.event_type in config.request_types
            is_success = event.event_type in config.success_types
            is_error = _is_error_event(event, config)

            if pending_request is None:
                if is_request:
                    pending_request = event
                elif is_success or is_error:
                    # A response with no preceding open request for this key.
                    pairs.append(_build_pair(None, event, key, config, is_error, duplicate=True))
                continue

            if is_success or is_error:
                pairs.append(_build_pair(pending_request, event, key, config, is_error, duplicate=False))
                pending_request = None
            elif is_request:
                # A second request before the first resolved -- the first is incomplete.
                pairs.append(_build_pair(pending_request, None, key, config, False, duplicate=False))
                pending_request = event

        if pending_request is not None:
            pairs.append(_build_pair(pending_request, None, key, config, False, duplicate=False))

    return pairs


def _build_pair(
    request: Optional[NormalizedEvent],
    response: Optional[NormalizedEvent],
    key: str,
    config: _DependencyConfig,
    is_error: bool,
    duplicate: bool,
) -> RequestResponsePair:
    if response is None:
        return RequestResponsePair(
            request_event_id=request.source_event_id if request else None,
            join_key=key,
            log_family=request.log_family.value if request else None,
            request_timestamp=request.event_timestamp if request else None,
            outcome=PairOutcome.INCOMPLETE,
            issuer_id=request.issuer_id if request else None,
            queue_name=request.queue_name if request else None,
        )

    latency_ms = None
    req_ts = _parse_ts(request.event_timestamp) if request else None
    resp_ts = _parse_ts(response.event_timestamp)
    if req_ts and resp_ts:
        latency_ms = round((resp_ts - req_ts).total_seconds() * 1000, 1)

    if is_error:
        outcome = PairOutcome.TIMEOUT if response.event_type in config.timeout_types else PairOutcome.ERROR
    else:
        outcome = PairOutcome.SUCCESS

    return RequestResponsePair(
        request_event_id=request.source_event_id if request else None,
        response_event_id=response.source_event_id,
        join_key=key,
        log_family=response.log_family.value,
        request_timestamp=request.event_timestamp if request else None,
        response_timestamp=response.event_timestamp,
        latency_ms=latency_ms,
        outcome=outcome,
        event_type=response.event_type,
        failure_signature=response.failure_signature,
        is_duplicate_response=duplicate,
        issuer_id=(request.issuer_id if request and request.issuer_id else response.issuer_id),
        queue_name=(request.queue_name if request and request.queue_name else response.queue_name),
    )


def compute_dependency_metrics(
    events: List[NormalizedEvent],
    dependency: Dependency,
    expected_latency_ms: Optional[int] = None,
) -> DependencyMetrics:
    """For every dependency: request_count, completed_responses,
    missing_responses, successful_responses, errors, incomplete_requests,
    median/p95 latency, error_rate, timeout_count, failure_signatures. A
    missing response is INCOMPLETE unless the pair resolved to an explicit
    error/timeout -- never fabricated as either."""
    threshold = expected_latency_ms if expected_latency_ms is not None else DEFAULT_EXPECTED_LATENCY_MS.get(dependency)
    pairs = _pair_dependency_events(events, dependency)

    config = DEPENDENCY_CONFIG[dependency]
    note = None
    if not config.request_types:
        note = (
            f"{dependency.value} has no request-boundary event in the current parser vocabulary -- only a "
            "success confirmation exists. request_count/missing_responses/latency are not computable and are "
            "reported as 0/None rather than fabricated."
        )

    # Duplicate responses (an extra response with no genuine open request
    # behind it) don't represent a real request/response round trip -- they
    # inform duplicate_responses only, never success/error/latency stats,
    # so completed_responses can never exceed request_count.
    genuine = [p for p in pairs if not p.is_duplicate_response]
    duplicates = [p for p in pairs if p.is_duplicate_response]

    request_count = sum(1 for p in genuine if p.request_event_id)
    successful = [p for p in genuine if p.outcome == PairOutcome.SUCCESS]
    errored = [p for p in genuine if p.outcome in (PairOutcome.ERROR, PairOutcome.TIMEOUT)]
    incomplete = [p for p in genuine if p.outcome == PairOutcome.INCOMPLETE]
    completed = successful + errored
    timeouts = [p for p in genuine if p.outcome == PairOutcome.TIMEOUT]

    latencies = [p.latency_ms for p in completed if p.latency_ms is not None]
    delayed = [p for p in completed if p.latency_ms is not None and threshold is not None and p.latency_ms > threshold]

    failure_sig_counts: Counter = Counter(p.failure_signature for p in errored if p.failure_signature)

    return DependencyMetrics(
        dependency=dependency,
        request_count=request_count if config.request_types else len(successful),
        completed_responses=len(completed),
        missing_responses=len(incomplete),
        successful_responses=len(successful),
        errors=len(errored),
        incomplete_requests=len(incomplete),
        median_latency_ms=round(statistics.median(latencies), 1) if latencies else None,
        p95_latency_ms=_percentile(latencies, 95),
        error_rate=round(len(errored) / len(completed), 4) if completed else None,
        timeout_count=len(timeouts),
        failure_signatures=dict(failure_sig_counts),
        duplicate_responses=len(duplicates),
        delayed_count=len(delayed),
        expected_latency_ms=threshold,
        pairs=pairs,
        note=note,
    )


def compute_dependency_time_buckets(
    events: List[NormalizedEvent],
    dependency: Dependency,
    bucket_minutes: int = 60,
    expected_latency_ms: Optional[int] = None,
) -> DependencyTimeSeriesReport:
    """Time-bucket breakdown for dependency health: volume, median, P95,
    error_rate, incomplete_rate per bucket -- lets "is V+ slow *right
    now*" be answered separately from "was V+ ever slow"."""
    metrics = compute_dependency_metrics(events, dependency, expected_latency_ms)
    pairs_with_time = [p for p in metrics.pairs if (p.request_timestamp or p.response_timestamp)]

    def _bucket_start(pair: RequestResponsePair) -> Optional[str]:
        ts = _parse_ts(pair.request_timestamp or pair.response_timestamp)
        if not ts:
            return None
        floored_minute = (ts.minute // bucket_minutes) * bucket_minutes
        bucket_dt = ts.replace(minute=0, second=0, microsecond=0) if bucket_minutes >= 60 else ts.replace(second=0, microsecond=0)
        if bucket_minutes < 60:
            bucket_dt = bucket_dt.replace(minute=floored_minute)
        return bucket_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    buckets: Dict[str, List[RequestResponsePair]] = defaultdict(list)
    for pair in pairs_with_time:
        key = _bucket_start(pair)
        if key:
            buckets[key].append(pair)

    bucket_reports = []
    for bucket_start in sorted(buckets.keys()):
        bucket_pairs = buckets[bucket_start]
        completed = [p for p in bucket_pairs if p.outcome in (PairOutcome.SUCCESS, PairOutcome.ERROR, PairOutcome.TIMEOUT)]
        errored = [p for p in bucket_pairs if p.outcome in (PairOutcome.ERROR, PairOutcome.TIMEOUT)]
        incomplete = [p for p in bucket_pairs if p.outcome == PairOutcome.INCOMPLETE]
        latencies = [p.latency_ms for p in completed if p.latency_ms is not None]
        total = len(bucket_pairs)
        bucket_reports.append(
            TimeBucketMetrics(
                bucket_start=bucket_start,
                volume=total,
                median_latency_ms=round(statistics.median(latencies), 1) if latencies else None,
                p95_latency_ms=_percentile(latencies, 95),
                error_rate=round(len(errored) / len(completed), 4) if completed else None,
                incomplete_rate=round(len(incomplete) / total, 4) if total else None,
            )
        )

    return DependencyTimeSeriesReport(dependency=dependency, bucket_minutes=bucket_minutes, buckets=bucket_reports)


def group_latency_by(metrics: DependencyMetrics, dimension: str) -> Dict[str, dict]:
    """Breaks a dependency's latency down by "issuer_id" or "queue_name"
    (or "log_family") -- answers "which issuer/host/queue has the highest
    latency" without needing a separate report type. Only completed pairs
    (success or error) with a resolved latency contribute; a missing
    response has no latency to attribute to any group."""
    if dimension not in ("issuer_id", "queue_name", "log_family"):
        raise ValueError(f"Unsupported grouping dimension: {dimension}")

    buckets: Dict[str, List[float]] = defaultdict(list)
    for pair in metrics.pairs:
        if pair.is_duplicate_response or pair.latency_ms is None:
            continue
        key = getattr(pair, dimension, None)
        if key:
            buckets[key].append(pair.latency_ms)

    return {
        key: {"median_ms": round(statistics.median(values), 1), "p95_ms": _percentile(values, 95), "count": len(values)}
        for key, values in buckets.items()
    }


def compute_all_dependencies(
    events: List[NormalizedEvent], expected_latency_ms: Optional[Dict[Dependency, int]] = None
) -> Dict[str, DependencyMetrics]:
    overrides = expected_latency_ms or {}
    return {
        dep.value: compute_dependency_metrics(events, dep, overrides.get(dep))
        for dep in Dependency
    }


# ---------------------------------------------------------------------------
# OTP handoff chain: OTP_GENERATED -> APPLICATION_QUEUE_CONFIRMED ->
# PROCESSOR_RECEIVED -> DOWNSTREAM_QUEUE_SELECTED -> VALIDATED
# ---------------------------------------------------------------------------
# Deliberately its own, more precise mapping than Phase 4's general
# lifecycle stage map: Phase 4 mapped an OTP Online Processor's OWN
# "queue"/"sms_queue_msg_id" events to APPLICATION_QUEUE_CONFIRMED (correct
# for a general "was the OTP queued anywhere" lifecycle view). Here, the
# processor's OWN queueing is specifically DOWNSTREAM_QUEUE_SELECTED -- the
# processor routing to ITS OWN downstream provider queue, which is a
# distinct, later step from the ORIGINATING APPLICATION (Cardinal/VFlex/
# Debit/Netcetera) confirming it queued the message TO the processor. This
# distinction is the whole point of Phase 5's queue-handoff analysis, so
# it isn't reused from lifecycle.py's coarser map.
_HANDOFF_MAPS: Dict[str, Dict[str, HandoffStage]] = {
    LogFamily.CARDINAL.value: {
        "otp_input": HandoffStage.OTP_GENERATED,
        "otp_queue": HandoffStage.APPLICATION_QUEUE_CONFIRMED,
        "otp_success": HandoffStage.VALIDATED,
    },
    LogFamily.VFLEX.value: {
        "sms_input": HandoffStage.OTP_GENERATED,
        "sms_queue": HandoffStage.APPLICATION_QUEUE_CONFIRMED,
        "otp_success": HandoffStage.VALIDATED,
    },
    LogFamily.DEBIT_PORTAL.value: {
        "msg_received_xml": HandoffStage.OTP_GENERATED,
        "sms_input_xml": HandoffStage.OTP_GENERATED,
        "email_xml": HandoffStage.OTP_GENERATED,
        "queue": HandoffStage.APPLICATION_QUEUE_CONFIRMED,
        "queue_msg_id": HandoffStage.APPLICATION_QUEUE_CONFIRMED,
        "otp_success": HandoffStage.VALIDATED,
    },
    LogFamily.NETCETERA_VPLUS.value: {
        "sms_input": HandoffStage.OTP_GENERATED,
        "email_message": HandoffStage.OTP_GENERATED,
        "sms_queue": HandoffStage.APPLICATION_QUEUE_CONFIRMED,
        "otp_success": HandoffStage.VALIDATED,
    },
    LogFamily.OTP_PROCESSOR.value: {
        "msg_received_sms_xml": HandoffStage.PROCESSOR_RECEIVED,  # the processor RECEIVING what the application queued
        "sms_input_xml": HandoffStage.PROCESSOR_RECEIVED,
        "email_xml": HandoffStage.PROCESSOR_RECEIVED,
        "queue": HandoffStage.DOWNSTREAM_QUEUE_SELECTED,  # the processor routing onward to its own downstream queue
        "sms_queue_msg_id": HandoffStage.DOWNSTREAM_QUEUE_SELECTED,
        "otp_success": HandoffStage.VALIDATED,
        "force_verify_by_mobile": HandoffStage.VALIDATED,
    },
}

_HANDOFF_ORDER: Tuple[HandoffStage, ...] = (
    HandoffStage.OTP_GENERATED,
    HandoffStage.APPLICATION_QUEUE_CONFIRMED,
    HandoffStage.PROCESSOR_RECEIVED,
    HandoffStage.DOWNSTREAM_QUEUE_SELECTED,
    HandoffStage.VALIDATED,
)
_APPLICATION_FAMILIES = {
    LogFamily.CARDINAL.value,
    LogFamily.VFLEX.value,
    LogFamily.DEBIT_PORTAL.value,
    LogFamily.NETCETERA_VPLUS.value,
}


def _map_to_handoff_stage(event: NormalizedEvent) -> Optional[HandoffStage]:
    return _HANDOFF_MAPS.get(event.log_family.value, {}).get(event.event_type or "")


def compute_otp_handoff_chain(events: List[NormalizedEvent]) -> QueueHandoffReport:
    """Joins application and processor events using the exact IA tracker
    (tracker_type == "IA") -- no fuzzy/composite matching, per the Phase 5
    spec. Never reports a "delivered" state: DOWNSTREAM_QUEUE_SELECTED
    means the processor selected/routed to its own outbound queue, NOT
    that the SMS/email actually reached the customer's device -- that
    evidence doesn't exist in any of these logs."""
    ia_events = [e for e in events if e.tracker_type == "IA" and e.tracker_no]

    tracker_events: Dict[str, List[NormalizedEvent]] = defaultdict(list)
    for event in ia_events:
        tracker_events[event.tracker_no].append(event)

    generated = application_queued = processor_received = downstream_routed = validated = 0
    queue_counter: Counter = Counter()
    tracker_records: List[TrackerHandoffRecord] = []
    unmatched: List[str] = []
    orphans: List[str] = []

    transition_samples: Dict[Tuple[str, str], List[float]] = defaultdict(list)

    for tracker_no, tracker_evts in tracker_events.items():
        tracker_evts_sorted = sorted(tracker_evts, key=_sort_key)
        stage_first_event: Dict[HandoffStage, NormalizedEvent] = {}
        for event in tracker_evts_sorted:
            stage = _map_to_handoff_stage(event)
            if stage and stage not in stage_first_event:
                stage_first_event[stage] = event
            if event.queue_name:
                queue_counter[event.queue_name] += 1

        has_application_event = any(e.log_family.value in _APPLICATION_FAMILIES for e in tracker_evts_sorted)
        has_processor_event = any(e.log_family.value == LogFamily.OTP_PROCESSOR.value for e in tracker_evts_sorted)

        if HandoffStage.OTP_GENERATED in stage_first_event:
            generated += 1
        if HandoffStage.APPLICATION_QUEUE_CONFIRMED in stage_first_event:
            application_queued += 1
        if HandoffStage.PROCESSOR_RECEIVED in stage_first_event:
            processor_received += 1
        if HandoffStage.DOWNSTREAM_QUEUE_SELECTED in stage_first_event:
            downstream_routed += 1
        if HandoffStage.VALIDATED in stage_first_event:
            validated += 1

        if HandoffStage.APPLICATION_QUEUE_CONFIRMED in stage_first_event and HandoffStage.PROCESSOR_RECEIVED not in stage_first_event:
            unmatched.append(tracker_no)
        if has_processor_event and not has_application_event:
            orphans.append(tracker_no)

        for stage_a, stage_b in zip(_HANDOFF_ORDER, _HANDOFF_ORDER[1:]):
            if stage_a in stage_first_event and stage_b in stage_first_event:
                ts_a = _parse_ts(stage_first_event[stage_a].event_timestamp)
                ts_b = _parse_ts(stage_first_event[stage_b].event_timestamp)
                if ts_a and ts_b:
                    transition_samples[(stage_a.value, stage_b.value)].append((ts_b - ts_a).total_seconds() * 1000)

        reached = None
        for stage in reversed(_HANDOFF_ORDER):
            if stage in stage_first_event:
                reached = stage.value
                break

        tracker_records.append(
            TrackerHandoffRecord(
                tracker_no=tracker_no,
                stage_event_ids={s.value: e.source_event_id for s, e in stage_first_event.items() if e.source_event_id},
                stage_timestamps={s.value: e.event_timestamp for s, e in stage_first_event.items() if e.event_timestamp},
                reached_stage=reached,
                has_application_event=has_application_event,
                has_processor_event=has_processor_event,
            )
        )

    transition_latencies = [
        TransitionLatency(
            from_stage=stage_a,
            to_stage=stage_b,
            sample_count=len(samples),
            median_ms=round(statistics.median(samples), 1) if samples else None,
            p95_ms=_percentile(samples, 95),
        )
        for (stage_a, stage_b), samples in transition_samples.items()
    ]

    return QueueHandoffReport(
        generated_messages=generated,
        application_queued_messages=application_queued,
        processor_received_messages=processor_received,
        downstream_routed_messages=downstream_routed,
        validated_messages=validated,
        unmatched_messages=len(unmatched),
        unmatched_tracker_nos=sorted(unmatched),
        orphan_messages=len(orphans),
        orphan_tracker_nos=sorted(orphans),
        queue_distribution=dict(queue_counter),
        transition_latencies=transition_latencies,
        tracker_records=sorted(tracker_records, key=lambda r: r.tracker_no),
    )
