"""
Tests for the Phase 5 deterministic operational/dependency analysis engine:
backend/analysis/dependency.py + backend/analysis/dependency_schema.py.

NOTE on timestamps: every ts_utc in this suite uses whole-second
granularity ("...:00Z"), matching the established system convention (see
backend/api/date_range.py's ISO_FMT and parser_OTP_Processor.py's own
docstring: ts_utc is always "%Y-%m-%dT%H:%M:%SZ", never fractional
seconds) -- the sort key used throughout Phases 3-5 is a plain string
comparison of this field, which is only chronologically correct at
consistent, whole-second granularity.
"""
from backend.analysis.dependency import (
    compute_all_dependencies,
    compute_dependency_metrics,
    compute_dependency_time_buckets,
    compute_otp_handoff_chain,
    group_latency_by,
)
from backend.analysis.dependency_schema import Dependency, PairOutcome
from backend.analysis.normalize import normalize_event
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent, derive_tracker_type_and_phase


def _ne(event_no, source_event_id, event_type, ts, tracker_no, level="INFO", log_family=LogFamily.CARDINAL, **overrides):
    tracker_type, phase = derive_tracker_type_and_phase(tracker_no)
    defaults = dict(
        source_file="sample.log",
        log_family=log_family,
        event_no=event_no,
        raw_reference=f"raw-{source_event_id}",
        source_event_id=source_event_id,
        batch_id="batch-1",
        event_timestamp=ts,
        level=level,
        event_type=event_type,
        tracker_no=tracker_no,
        tracker_type=tracker_type,
        phase=phase,
    )
    defaults.update(overrides)
    return NormalizedEvent(**defaults)


def _canonical(source_system, event_id, ts, component, correlation_id, details, level="INFO"):
    return {
        "event_id": event_id,
        "batch_id": "batch-demo",
        "file_name": f"{source_system}.log",
        "line_no": 1,
        "ts_utc": ts,
        "level": level,
        "source_system": source_system,
        "component": component,
        "message": "demo",
        "raw": f"raw-{event_id}",
        "attributes": {"correlation_id": correlation_id, "details": details},
    }


# ---------------------------------------------------------------------------
# Missing responses
# ---------------------------------------------------------------------------

def test_missing_response_is_incomplete_not_error():
    events = [_ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1")]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)

    assert metrics.request_count == 1
    assert metrics.completed_responses == 0
    assert metrics.missing_responses == 1
    assert metrics.incomplete_requests == 1
    assert metrics.errors == 0
    assert metrics.successful_responses == 0
    pair = metrics.pairs[0]
    assert pair.outcome == PairOutcome.INCOMPLETE
    assert pair.latency_ms is None  # never fabricated


def test_missing_response_does_not_count_as_error_rate_denominator():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "r2", "vplus_input", "2026-08-21T09:01:00Z", "SU2"),
        _ne(3, "s2", "vplus_response", "2026-08-21T09:01:01Z", "SU2"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    # error_rate is errors/completed_responses -- the missing SU1 pair must
    # not silently deflate or otherwise pollute it.
    assert metrics.completed_responses == 1
    assert metrics.error_rate == 0.0
    assert metrics.missing_responses == 1


def test_second_request_before_first_resolves_makes_first_incomplete():
    events = [
        _ne(1, "r1a", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "r1b", "vplus_input", "2026-08-21T09:00:05Z", "SU1"),  # same tracker, no response to r1a yet
        _ne(3, "s1b", "vplus_response", "2026-08-21T09:00:06Z", "SU1"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    assert metrics.request_count == 2
    assert metrics.missing_responses == 1
    assert metrics.successful_responses == 1


# ---------------------------------------------------------------------------
# Delayed responses
# ---------------------------------------------------------------------------

def test_delayed_response_flagged_and_counted():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "s1", "vplus_response", "2026-08-21T09:00:03Z", "SU1"),  # 3000ms > default 1000ms threshold
    ]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    assert metrics.delayed_count == 1
    assert metrics.pairs[0].latency_ms == 3000.0
    assert metrics.pairs[0].outcome == PairOutcome.SUCCESS


def test_custom_expected_latency_threshold_changes_delayed_count():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "s1", "vplus_response", "2026-08-21T09:00:03Z", "SU1"),
    ]
    lenient = compute_dependency_metrics(events, Dependency.V_PLUS, expected_latency_ms=5000)
    assert lenient.delayed_count == 0
    strict = compute_dependency_metrics(events, Dependency.V_PLUS, expected_latency_ms=500)
    assert strict.delayed_count == 1


def test_median_and_p95_latency_computed_correctly():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "s1", "vplus_response", "2026-08-21T09:00:01Z", "SU1"),  # 1000ms
        _ne(3, "r2", "vplus_input", "2026-08-21T09:01:00Z", "SU2"),
        _ne(4, "s2", "vplus_response", "2026-08-21T09:01:03Z", "SU2"),  # 3000ms
        _ne(5, "r3", "vplus_input", "2026-08-21T09:02:00Z", "SU3"),
        _ne(6, "s3", "vplus_response", "2026-08-21T09:02:05Z", "SU3"),  # 5000ms
    ]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    assert metrics.median_latency_ms == 3000.0
    assert metrics.p95_latency_ms == 5000.0


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

def test_explicit_timeout_event_classified_as_timeout_not_incomplete():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "t1", "vplus_mq_timeout", "2026-08-21T09:00:05Z", "SU1", level="ERROR", failure_signature="cardinal:vplus_mq_timeout:x"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    assert metrics.missing_responses == 0  # NOT incomplete -- explicit timeout evidence exists
    assert metrics.timeout_count == 1
    assert metrics.errors == 1
    assert metrics.pairs[0].outcome == PairOutcome.TIMEOUT
    assert metrics.pairs[0].latency_ms == 5000.0  # timeouts still get a real latency -- both timestamps exist


def test_non_timeout_error_type_not_counted_as_timeout():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "e1", "web_exception", "2026-08-21T09:00:02Z", "SU1", level="ERROR"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    assert metrics.errors == 1
    assert metrics.timeout_count == 0


def test_otp_processor_error_detected_via_failure_signature_fallback():
    """OTP Online Processor has no dependency-specific error event_type in
    its vocabulary -- error detection must fall back to Phase 2's
    failure_signature, not silently report zero errors forever."""
    events = [
        _ne(1, "r1", "msg_received_sms_xml", "2026-08-21T09:00:00Z", "IA1", log_family=LogFamily.OTP_PROCESSOR),
        _ne(2, "e1", "other", "2026-08-21T09:00:02Z", "IA1", log_family=LogFamily.OTP_PROCESSOR, level="WARN", failure_signature="otp_online_processor:other:unknown"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.OTP_ONLINE_PROCESSOR)
    assert metrics.errors == 1
    assert metrics.missing_responses == 0


# ---------------------------------------------------------------------------
# Duplicate responses
# ---------------------------------------------------------------------------

def test_duplicate_response_flagged_and_excluded_from_success_count():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "s1a", "vplus_response", "2026-08-21T09:00:01Z", "SU1"),
        _ne(3, "s1b", "vplus_response", "2026-08-21T09:00:02Z", "SU1"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    assert metrics.request_count == 1
    assert metrics.successful_responses == 1  # only the first response counts as resolving the request
    assert metrics.completed_responses == 1
    assert metrics.duplicate_responses == 1
    duplicate_pair = next(p for p in metrics.pairs if p.is_duplicate_response)
    assert duplicate_pair.response_event_id == "s1b"
    assert duplicate_pair.request_event_id is None


def test_response_with_no_request_at_all_is_a_duplicate_orphan_response():
    events = [_ne(1, "s1", "vplus_response", "2026-08-21T09:00:00Z", "SU1")]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    assert metrics.request_count == 0
    assert metrics.duplicate_responses == 1
    assert metrics.successful_responses == 0  # duplicates never count toward genuine success


# ---------------------------------------------------------------------------
# Queue gaps -- unmatched / orphan handoff messages
# ---------------------------------------------------------------------------

def test_unmatched_application_message_never_reaches_processor():
    events = [
        _ne(1, "a1", "otp_input", "2026-08-21T09:00:00Z", "IA1"),
        _ne(2, "a2", "otp_queue", "2026-08-21T09:00:01Z", "IA1"),
    ]
    report = compute_otp_handoff_chain(events)
    assert report.application_queued_messages == 1
    assert report.processor_received_messages == 0
    assert report.unmatched_messages == 1
    assert report.unmatched_tracker_nos == ["IA1"]
    assert report.orphan_messages == 0


def test_orphan_processor_message_has_no_application_event():
    events = [_ne(1, "p1", "msg_received_sms_xml", "2026-08-21T09:00:00Z", "IA9", log_family=LogFamily.OTP_PROCESSOR)]
    report = compute_otp_handoff_chain(events)
    assert report.processor_received_messages == 1
    assert report.orphan_messages == 1
    assert report.orphan_tracker_nos == ["IA9"]
    assert report.unmatched_messages == 0


def test_matched_full_chain_is_neither_unmatched_nor_orphan():
    events = [
        _ne(1, "a1", "otp_input", "2026-08-21T09:00:00Z", "IA1"),
        _ne(2, "a2", "otp_queue", "2026-08-21T09:00:01Z", "IA1"),
        _ne(3, "p1", "msg_received_sms_xml", "2026-08-21T09:00:03Z", "IA1", log_family=LogFamily.OTP_PROCESSOR),
    ]
    report = compute_otp_handoff_chain(events)
    assert report.unmatched_messages == 0
    assert report.orphan_messages == 0
    record = report.tracker_records[0]
    assert record.has_application_event and record.has_processor_event


def test_su_tracker_excluded_from_ia_only_handoff_analysis():
    """Join must use the EXACT IA tracker -- an SU (StepUp) tracker must
    never be pulled into the OTP handoff chain, even if it superficially
    looks like a queue-related event."""
    events = [
        _ne(1, "a1", "otp_queue", "2026-08-21T09:00:00Z", "SU1"),  # SU, not IA
    ]
    report = compute_otp_handoff_chain(events)
    assert report.application_queued_messages == 0
    assert report.generated_messages == 0
    assert report.tracker_records == []


def test_no_delivered_state_exists_anywhere_in_the_handoff_model():
    from backend.analysis.dependency_schema import HandoffStage

    stage_values = {s.value for s in HandoffStage}
    assert "DELIVERED" not in stage_values
    assert stage_values == {
        "OTP_GENERATED",
        "APPLICATION_QUEUE_CONFIRMED",
        "PROCESSOR_RECEIVED",
        "DOWNSTREAM_QUEUE_SELECTED",
        "VALIDATED",
    }


def test_downstream_routed_is_distinct_from_processor_received():
    events = [
        _ne(1, "p1", "msg_received_sms_xml", "2026-08-21T09:00:00Z", "IA1", log_family=LogFamily.OTP_PROCESSOR),
        _ne(2, "p2", "queue", "2026-08-21T09:00:02Z", "IA1", log_family=LogFamily.OTP_PROCESSOR),
    ]
    report = compute_otp_handoff_chain(events)
    assert report.processor_received_messages == 1
    assert report.downstream_routed_messages == 1
    transition = next(t for t in report.transition_latencies if t.from_stage == "PROCESSOR_RECEIVED")
    assert transition.to_stage == "DOWNSTREAM_QUEUE_SELECTED"
    assert transition.median_ms == 2000.0


def test_queue_distribution_counts_by_queue_name():
    events = [
        _ne(1, "p1", "queue", "2026-08-21T09:00:00Z", "IA1", log_family=LogFamily.OTP_PROCESSOR, queue_name="mq-a"),
        _ne(2, "p2", "queue", "2026-08-21T09:01:00Z", "IA2", log_family=LogFamily.OTP_PROCESSOR, queue_name="mq-a"),
        _ne(3, "p3", "queue", "2026-08-21T09:02:00Z", "IA3", log_family=LogFamily.OTP_PROCESSOR, queue_name="mq-b"),
    ]
    report = compute_otp_handoff_chain(events)
    assert report.queue_distribution == {"mq-a": 2, "mq-b": 1}


# ---------------------------------------------------------------------------
# Database/SQL -- no request boundary marker exists
# ---------------------------------------------------------------------------

def test_database_sql_never_fabricates_latency_or_request_count():
    events = [_ne(1, "d1", "sql_connection_success", "2026-08-21T09:00:00Z", None, log_family=LogFamily.VFLEX)]
    metrics = compute_dependency_metrics(events, Dependency.DATABASE_SQL)
    assert metrics.successful_responses == 1
    assert metrics.median_latency_ms is None
    assert metrics.p95_latency_ms is None
    assert metrics.missing_responses == 0
    assert metrics.note is not None and "no request-boundary" in metrics.note


# ---------------------------------------------------------------------------
# Other dependencies -- Postilion, Bank API, OOB API
# ---------------------------------------------------------------------------

def test_postilion_request_response_pairing():
    events = [
        _ne(1, "r1", "debit_request_json", "2026-08-21T09:00:00Z", "IA1", log_family=LogFamily.DEBIT_PORTAL),
        _ne(2, "s1", "debit_response_json", "2026-08-21T09:00:02Z", "IA1", log_family=LogFamily.DEBIT_PORTAL),
    ]
    metrics = compute_dependency_metrics(events, Dependency.POSTILION)
    assert metrics.successful_responses == 1
    assert metrics.pairs[0].latency_ms == 2000.0


def test_bank_api_error_response_counted_as_error_not_success():
    events = [
        _ne(1, "r1", "bank_request", "2026-08-21T09:00:00Z", "SU1", log_family=LogFamily.VFLEX),
        _ne(2, "e1", "bank_api_error_response", "2026-08-21T09:00:01Z", "SU1", log_family=LogFamily.VFLEX, level="ERROR"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.BANK_API)
    assert metrics.errors == 1
    assert metrics.successful_responses == 0


def test_oob_api_status_poll_excluded_from_pairing():
    """oob_status_poll is an intermediate wait signal, not its own
    request -- it must not create a phantom incomplete request."""
    events = [
        _ne(1, "r1", "oob_authenticate_api", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "poll1", "oob_status_poll", "2026-08-21T09:00:02Z", "SU1"),
        _ne(3, "s1", "oob_validate_api", "2026-08-21T09:00:10Z", "SU1"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.OOB_API)
    assert metrics.request_count == 1
    assert metrics.successful_responses == 1
    assert metrics.missing_responses == 0


# ---------------------------------------------------------------------------
# Time-bucket analysis
# ---------------------------------------------------------------------------

def test_time_buckets_split_by_hour_and_compute_per_bucket_metrics():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),
        _ne(2, "s1", "vplus_response", "2026-08-21T09:00:01Z", "SU1"),
        _ne(3, "r2", "vplus_input", "2026-08-21T10:30:00Z", "SU2"),
        _ne(4, "t2", "vplus_mq_timeout", "2026-08-21T10:30:05Z", "SU2", level="ERROR"),
    ]
    report = compute_dependency_time_buckets(events, Dependency.V_PLUS, bucket_minutes=60)
    assert len(report.buckets) == 2
    first, second = report.buckets
    assert first.bucket_start == "2026-08-21T09:00:00Z"
    assert first.error_rate == 0.0
    assert second.bucket_start == "2026-08-21T10:00:00Z"
    assert second.error_rate == 1.0
    assert second.median_latency_ms == 5000.0


def test_time_bucket_incomplete_rate():
    events = [
        _ne(1, "r1", "vplus_input", "2026-08-21T09:00:00Z", "SU1"),  # missing response
        _ne(2, "r2", "vplus_input", "2026-08-21T09:05:00Z", "SU2"),
        _ne(3, "s2", "vplus_response", "2026-08-21T09:05:01Z", "SU2"),
    ]
    report = compute_dependency_time_buckets(events, Dependency.V_PLUS, bucket_minutes=60)
    assert len(report.buckets) == 1
    assert report.buckets[0].volume == 2
    assert report.buckets[0].incomplete_rate == 0.5


# ---------------------------------------------------------------------------
# compute_all_dependencies
# ---------------------------------------------------------------------------

def test_compute_all_dependencies_covers_every_dependency():
    result = compute_all_dependencies([])
    assert set(result.keys()) == {d.value for d in Dependency}
    for metrics in result.values():
        assert metrics.request_count == 0


# ---------------------------------------------------------------------------
# Realistic end-to-end: Phase 2 normalize_event() -> Phase 5 dependency analysis
# ---------------------------------------------------------------------------

def test_realistic_vplus_answers_is_v_plus_slow():
    events = [
        normalize_event(_canonical("cardinal_stepup_oob_log", "r1", "2026-08-21T09:00:00Z", "vplus_input", None, {"flow": {"trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "s1", "2026-08-21T09:00:04Z", "vplus_response", None, {"flow": {"trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "r2", "2026-08-21T09:01:00Z", "vplus_input", None, {"flow": {"trackers": ["SU2"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "s2", "2026-08-21T09:01:03Z", "vplus_response", None, {"flow": {"trackers": ["SU2"]}})),
    ]
    metrics = compute_dependency_metrics(events, Dependency.V_PLUS)
    # "Is V+ slow?" -- yes, both pairs exceed the 1000ms default expectation.
    assert metrics.delayed_count == 2
    assert metrics.median_latency_ms > metrics.expected_latency_ms


def test_group_latency_by_issuer_identifies_the_slowest_issuer():
    events = [
        _ne(1, "r1", "bank_request", "2026-08-21T09:00:00Z", "SU1", log_family=LogFamily.VFLEX, issuer_id="ISS-FAST"),
        _ne(2, "s1", "bank_api_success_response", "2026-08-21T09:00:01Z", "SU1", log_family=LogFamily.VFLEX, issuer_id="ISS-FAST"),
        _ne(3, "r2", "bank_request", "2026-08-21T09:01:00Z", "SU2", log_family=LogFamily.VFLEX, issuer_id="ISS-SLOW"),
        _ne(4, "s2", "bank_api_success_response", "2026-08-21T09:01:05Z", "SU2", log_family=LogFamily.VFLEX, issuer_id="ISS-SLOW"),
    ]
    metrics = compute_dependency_metrics(events, Dependency.BANK_API)
    by_issuer = group_latency_by(metrics, "issuer_id")

    assert by_issuer["ISS-FAST"]["median_ms"] == 1000.0
    assert by_issuer["ISS-SLOW"]["median_ms"] == 5000.0
    slowest = max(by_issuer, key=lambda k: by_issuer[k]["median_ms"])
    assert slowest == "ISS-SLOW"


def test_group_latency_by_excludes_missing_and_duplicate_pairs():
    events = [
        _ne(1, "r1", "bank_request", "2026-08-21T09:00:00Z", "SU1", log_family=LogFamily.VFLEX, issuer_id="ISS-1"),
        _ne(2, "r2", "bank_request", "2026-08-21T09:01:00Z", "SU2", log_family=LogFamily.VFLEX, issuer_id="ISS-2"),  # missing response
        _ne(3, "s1a", "bank_api_success_response", "2026-08-21T09:02:00Z", "SU3", log_family=LogFamily.VFLEX, issuer_id="ISS-3"),  # orphan/duplicate, no request
    ]
    metrics = compute_dependency_metrics(events, Dependency.BANK_API)
    by_issuer = group_latency_by(metrics, "issuer_id")
    assert "ISS-2" not in by_issuer  # no latency exists for a missing response
    assert "ISS-3" not in by_issuer  # duplicate/orphan response excluded


def test_realistic_which_dependency_has_highest_p95():
    events = [
        normalize_event(_canonical("cardinal_stepup_oob_log", "r1", "2026-08-21T09:00:00Z", "vplus_input", None, {"flow": {"trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "s1", "2026-08-21T09:00:01Z", "vplus_response", None, {"flow": {"trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "r2", "2026-08-21T09:00:00Z", "oob_authenticate_api", None, {"flow": {"trackers": ["SU2"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "s2", "2026-08-21T09:00:30Z", "oob_validate_api", None, {"flow": {"trackers": ["SU2"]}})),
    ]
    all_metrics = compute_all_dependencies(events)
    p95_by_dep = {name: m.p95_latency_ms for name, m in all_metrics.items() if m.p95_latency_ms is not None}
    highest = max(p95_by_dep, key=p95_by_dep.get)
    assert highest == Dependency.OOB_API.value
