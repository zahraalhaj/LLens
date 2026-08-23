"""
Tests for the Phase 8 recurring incident/pattern analysis engine:
backend/analysis/pattern.py + backend/analysis/pattern_schema.py.
"""
from backend.analysis.correlate import correlate_events
from backend.analysis.failure import analyze_failures
from backend.analysis.lifecycle import reconstruct_lifecycles
from backend.analysis.normalize import normalize_event
from backend.analysis.pattern import (
    CONCENTRATION_THRESHOLD,
    MIN_RELIABLE_SAMPLE_SIZE,
    analyze_recurring_patterns,
    assess_incident,
    rank_patterns,
)
from backend.analysis.pattern_schema import GroupingDimension, RecurringPattern


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


def _timeout_event(txn, ts, issuer=None, merchant=None):
    flow = {"transaction_id": txn}
    if issuer:
        flow["issuer_id"] = issuer
    if merchant:
        flow["merchant"] = {"name": merchant}
    return normalize_event(_canonical("cardinal_stepup_oob_log", f"{txn}-err", ts, "vplus_mq_timeout", txn, {"flow": flow}, level="ERROR"))


def _clean_event(txn, ts):
    return normalize_event(_canonical("cardinal_stepup_oob_log", f"{txn}-ok", ts, "otp_success", txn, {"flow": {"transaction_id": txn}}))


def _analyze(events, lifecycles=None, **kwargs):
    result = correlate_events(events)
    failure_result = analyze_failures(events, flows=result.flows)
    lcs = lifecycles if lifecycles is not None else reconstruct_lifecycles(result.flows, events)
    pattern_result = analyze_recurring_patterns(result.flows, events, failure_result.findings, lifecycles=lcs, **kwargs)
    return result, failure_result, pattern_result


# ---------------------------------------------------------------------------
# Isolated incident vs. recurring failure
# ---------------------------------------------------------------------------

def test_single_occurrence_is_isolated():
    events = [_timeout_event("TXN-A", "2026-08-21T09:00:00Z"), _clean_event("TXN-B", "2026-08-21T09:01:00Z")]
    result, failure_result, pattern_result = _analyze(events)

    flow_a = next(f for f in result.flows if f.transaction_id == "TXN-A")
    findings_a = [f for f in failure_result.findings if flow_a.flow_id in f.affected_flow_ids]
    assessment = assess_incident(flow_a.flow_id, pattern_result, findings_a)

    assert assessment.is_isolated is True
    assert "1" in assessment.statement or "isolated" in assessment.statement.lower()
    assert str(pattern_result.total_flows_analyzed) in assessment.statement


def test_repeated_failure_is_not_isolated_and_states_denominator():
    events = [
        _timeout_event("TXN-A", "2026-08-21T09:00:00Z"),
        _timeout_event("TXN-B", "2026-08-21T09:01:00Z"),
        _clean_event("TXN-C", "2026-08-21T09:02:00Z"),
    ]
    result, failure_result, pattern_result = _analyze(events)

    flow_a = next(f for f in result.flows if f.transaction_id == "TXN-A")
    findings_a = [f for f in failure_result.findings if flow_a.flow_id in f.affected_flow_ids]
    assessment = assess_incident(flow_a.flow_id, pattern_result, findings_a)

    assert assessment.is_isolated is False
    assert "2/3" in assessment.statement or ("2" in assessment.statement and "3" in assessment.statement)


def test_flow_with_no_findings_is_isolated_by_default():
    events = [_clean_event("TXN-A", "2026-08-21T09:00:00Z"), _clean_event("TXN-B", "2026-08-21T09:01:00Z")]
    result, failure_result, pattern_result = _analyze(events)
    flow_a = next(f for f in result.flows if f.transaction_id == "TXN-A")
    assessment = assess_incident(flow_a.flow_id, pattern_result, [])
    assert assessment.is_isolated is True


# ---------------------------------------------------------------------------
# Denominator always shown; failure_rate always paired with total_flows
# ---------------------------------------------------------------------------

def test_failure_rate_always_accompanied_by_denominator():
    events = [_timeout_event(f"TXN-{i}", "2026-08-21T09:00:00Z") for i in range(3)] + [
        _clean_event(f"TXN-CLEAN-{i}", "2026-08-21T09:00:00Z") for i in range(7)
    ]
    _, _, pattern_result = _analyze(events)
    pattern = pattern_result.patterns[0]
    assert pattern.total_flows == 10
    assert pattern.affected_flows == 3
    assert pattern.failure_rate == 0.3


# ---------------------------------------------------------------------------
# Do not rank a tiny sample as a major issue simply because its percentage is high
# ---------------------------------------------------------------------------

def test_high_volume_pattern_outranks_tiny_high_percentage_pattern():
    events = [_timeout_event(f"TXN-BIG-{i}", "2026-08-21T09:00:00Z") for i in range(20)]
    events.append(
        normalize_event(_canonical("cardinal_stepup_oob_log", "tiny-a", "2026-08-21T09:00:00Z", "oob_http_error", "TXN-TINY", {"flow": {"transaction_id": "TXN-TINY"}}, level="ERROR"))
    )
    events += [_clean_event(f"TXN-CLEAN-{i}", "2026-08-21T09:00:00Z") for i in range(479)]  # total 500 flows

    _, _, pattern_result = _analyze(events)
    assert pattern_result.total_flows_analyzed == 500

    ranked_names = [p.pattern for p in pattern_result.patterns]
    assert ranked_names.index("V_PLUS_MQ_TIMEOUT") < ranked_names.index("OOB_HTTP_ERROR")
    assert pattern_result.patterns[0].rank == 1
    assert pattern_result.patterns[1].rank == 2


def test_low_sample_pattern_carries_explicit_warning():
    events = [_timeout_event(f"TXN-BIG-{i}", "2026-08-21T09:00:00Z") for i in range(10)]
    events.append(
        normalize_event(_canonical("cardinal_stepup_oob_log", "tiny-a", "2026-08-21T09:00:00Z", "oob_http_error", "TXN-TINY", {"flow": {"transaction_id": "TXN-TINY"}}, level="ERROR"))
    )
    _, _, pattern_result = _analyze(events)

    big = next(p for p in pattern_result.patterns if p.pattern == "V_PLUS_MQ_TIMEOUT")
    tiny = next(p for p in pattern_result.patterns if p.pattern == "OOB_HTTP_ERROR")
    assert big.low_sample_warning is None
    assert tiny.low_sample_warning is not None
    assert str(MIN_RELIABLE_SAMPLE_SIZE) in tiny.low_sample_warning
    assert "1" in tiny.low_sample_warning  # states its own tiny count, not hidden


def test_ranking_priority_affected_flows_before_failure_rate():
    """Two patterns sharing the same total_flows: one with MORE affected
    flows but a lower rate (impossible to construct with a shared
    denominator, so this instead verifies the documented tie-break order
    directly via rank_patterns())."""
    a = RecurringPattern(pattern="A", affected_flows=10, total_flows=100, failure_rate=0.10, last_seen="2026-08-01T00:00:00Z")
    b = RecurringPattern(pattern="B", affected_flows=3, total_flows=100, failure_rate=0.03, last_seen="2026-08-21T00:00:00Z")
    ranked = rank_patterns([b, a])
    assert [p.pattern for p in ranked] == ["A", "B"]
    assert ranked[0].rank == 1 and ranked[1].rank == 2


def test_ranking_recency_tiebreak_when_flows_and_rate_equal():
    a = RecurringPattern(pattern="OLDER", affected_flows=5, total_flows=100, failure_rate=0.05, last_seen="2026-08-01T00:00:00Z")
    b = RecurringPattern(pattern="NEWER", affected_flows=5, total_flows=100, failure_rate=0.05, last_seen="2026-08-21T00:00:00Z")
    ranked = rank_patterns([a, b])
    assert ranked[0].pattern == "NEWER"


# ---------------------------------------------------------------------------
# Grouping dimensions: issuer, merchant, dependency, queue, auth method, time window
# ---------------------------------------------------------------------------

def test_which_issuers_are_affected():
    events = [
        _timeout_event("TXN-A", "2026-08-21T09:00:00Z", issuer="ISS1"),
        _timeout_event("TXN-B", "2026-08-21T09:01:00Z", issuer="ISS1"),
        _timeout_event("TXN-C", "2026-08-21T09:02:00Z", issuer="ISS2"),
        _clean_event("TXN-D", "2026-08-21T09:03:00Z"),
    ]
    _, _, pattern_result = _analyze(events)
    assert pattern_result.by_issuer == {"ISS1": 2, "ISS2": 1}
    pattern = pattern_result.patterns[0]
    assert set(pattern.affected_issuers) == {"ISS1", "ISS2"}


def test_which_merchants_are_affected():
    events = [
        _timeout_event("TXN-A", "2026-08-21T09:00:00Z", merchant="ShopOne"),
        _timeout_event("TXN-B", "2026-08-21T09:01:00Z", merchant="ShopTwo"),
    ]
    _, _, pattern_result = _analyze(events)
    assert pattern_result.by_merchant == {"ShopOne": 1, "ShopTwo": 1}


def test_which_dependency_is_involved_reuses_phase5_definition():
    events = [_timeout_event("TXN-A", "2026-08-21T09:00:00Z")]
    _, _, pattern_result = _analyze(events)
    assert pattern_result.by_dependency == {"V_PLUS": 1}
    pattern = pattern_result.patterns[0]
    assert pattern.affected_dependencies == ["V_PLUS"]


def test_issuer_concentration_detected_above_threshold():
    events = [_timeout_event(f"TXN-{i}", "2026-08-21T09:00:00Z", issuer="ISS1") for i in range(8)]
    events += [_timeout_event(f"TXN-OTHER-{i}", "2026-08-21T09:00:00Z", issuer="ISS2") for i in range(2)]
    _, _, pattern_result = _analyze(events)
    pattern = pattern_result.patterns[0]
    issuer_concentration = next(c for c in pattern.concentration if c.dimension == GroupingDimension.ISSUER)
    assert issuer_concentration.dominant_value == "ISS1"
    assert issuer_concentration.ratio == 0.8
    assert issuer_concentration.ratio >= CONCENTRATION_THRESHOLD
    assert issuer_concentration.is_concentrated is True
    assert issuer_concentration.sample_count == 10


def test_not_concentrated_when_evenly_spread():
    events = [_timeout_event(f"TXN-{i}", "2026-08-21T09:00:00Z", issuer=f"ISS{i}") for i in range(5)]
    _, _, pattern_result = _analyze(events)
    pattern = pattern_result.patterns[0]
    issuer_concentration = next(c for c in pattern.concentration if c.dimension == GroupingDimension.ISSUER)
    assert issuer_concentration.is_concentrated is False


def test_time_window_concentration_when_all_in_one_bucket():
    events = [_timeout_event(f"TXN-{i}", "2026-08-21T09:00:00Z") for i in range(5)]
    _, _, pattern_result = _analyze(events, time_bucket_minutes=1440)
    pattern = pattern_result.patterns[0]
    time_concentration = next(c for c in pattern.concentration if c.dimension == GroupingDimension.TIME_WINDOW)
    assert time_concentration.is_concentrated is True
    assert time_concentration.ratio == 1.0


def test_time_window_not_concentrated_when_spread_across_days():
    events = [
        _timeout_event("TXN-1", "2026-08-01T09:00:00Z"),
        _timeout_event("TXN-2", "2026-08-05T09:00:00Z"),
        _timeout_event("TXN-3", "2026-08-10T09:00:00Z"),
        _timeout_event("TXN-4", "2026-08-15T09:00:00Z"),
        _timeout_event("TXN-5", "2026-08-20T09:00:00Z"),
    ]
    _, _, pattern_result = _analyze(events, time_bucket_minutes=1440)
    pattern = pattern_result.patterns[0]
    time_concentration = next(c for c in pattern.concentration if c.dimension == GroupingDimension.TIME_WINDOW)
    assert time_concentration.is_concentrated is False


def test_authentication_method_concentration():
    events = []
    for i in range(6):
        events.append(
            normalize_event(
                _canonical(
                    "cardinal_stepup_oob_log", f"otp-{i}-err", "2026-08-21T09:00:00Z", "vplus_mq_timeout", f"TXN-OTP-{i}",
                    {"flow": {"transaction_id": f"TXN-OTP-{i}", "authentication": {"type": "OTP"}}}, level="ERROR",
                )
            )
        )
    _, _, pattern_result = _analyze(events)
    pattern = pattern_result.patterns[0]
    auth_concentration = next(c for c in pattern.concentration if c.dimension == GroupingDimension.AUTHENTICATION_METHOD)
    assert auth_concentration.dominant_value == "OTP"
    assert auth_concentration.is_concentrated is True


def test_queue_concentration_and_by_queue_breakdown():
    events = [
        normalize_event(
            _canonical(
                "otp_online_processor", "q1-err", "2026-08-21T09:00:00Z", "other", None,
                {"tracker_no": "IA1", "parse_error": "boom", "record": {"queue": "mq-a"}}, level="WARN",
            )
        ),
        normalize_event(
            _canonical(
                "otp_online_processor", "q2-err", "2026-08-21T09:01:00Z", "other", None,
                {"tracker_no": "IA2", "parse_error": "boom", "record": {"queue": "mq-a"}}, level="WARN",
            )
        ),
    ]
    _, _, pattern_result = _analyze(events)
    assert pattern_result.by_queue == {"mq-a": 2}
    pattern = pattern_result.patterns[0]
    queue_concentration = next(c for c in pattern.concentration if c.dimension == GroupingDimension.QUEUE)
    assert queue_concentration.dominant_value == "mq-a"
    assert queue_concentration.is_concentrated is True


def test_lifecycle_stage_grouping_uses_phase4_last_confirmed_stage():
    events = [_timeout_event("TXN-A", "2026-08-21T09:00:00Z")]
    result = correlate_events(events)
    lifecycles = reconstruct_lifecycles(result.flows, events)
    failure_result = analyze_failures(events, flows=result.flows)
    pattern_result = analyze_recurring_patterns(result.flows, events, failure_result.findings, lifecycles=lifecycles)

    stage_dim = next(c for c in pattern_result.patterns[0].concentration if c.dimension == GroupingDimension.LIFECYCLE_STAGE)
    assert stage_dim.dominant_value in pattern_result.by_lifecycle_stage
    assert stage_dim.dominant_value != "PARTIAL"  # would indicate the old correlation_status bug, not a real lifecycle stage


def test_lifecycle_stage_falls_back_to_unknown_without_lifecycles_provided():
    events = [_timeout_event("TXN-A", "2026-08-21T09:00:00Z")]
    result = correlate_events(events)
    failure_result = analyze_failures(events, flows=result.flows)
    pattern_result = analyze_recurring_patterns(result.flows, events, failure_result.findings, lifecycles=None)
    assert pattern_result.by_lifecycle_stage == {"UNKNOWN": 1}


# ---------------------------------------------------------------------------
# Representative evidence
# ---------------------------------------------------------------------------

def test_representative_evidence_present_and_traceable():
    events = [_timeout_event("TXN-A", "2026-08-21T09:00:00Z")]
    _, _, pattern_result = _analyze(events)
    pattern = pattern_result.patterns[0]
    assert len(pattern.representative_evidence) >= 1
    assert pattern.representative_evidence[0].event_ids == ["TXN-A-err"]
    assert pattern.representative_evidence[0].source_files == ["cardinal_stepup_oob_log.log"]


# ---------------------------------------------------------------------------
# Aggregation discipline -- flow counts, not raw error-line counts
# ---------------------------------------------------------------------------

def test_pattern_counts_flows_not_raw_error_lines():
    """Reuses Phase 6's own flow-level aggregation -- a flow with 3 retried
    timeout lines must still count as ONE affected flow here."""
    events = [
        normalize_event(_canonical("cardinal_stepup_oob_log", "a1", "2026-08-21T09:00:00Z", "vplus_input", "TXN-A", {"flow": {"transaction_id": "TXN-A"}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "a2", "2026-08-21T09:00:02Z", "vplus_mq_timeout", "TXN-A", {"flow": {"transaction_id": "TXN-A"}}, level="ERROR")),
        normalize_event(_canonical("cardinal_stepup_oob_log", "a3", "2026-08-21T09:00:04Z", "vplus_mq_timeout", "TXN-A", {"flow": {"transaction_id": "TXN-A"}}, level="ERROR")),
        normalize_event(_canonical("cardinal_stepup_oob_log", "a4", "2026-08-21T09:00:06Z", "vplus_mq_timeout", "TXN-A", {"flow": {"transaction_id": "TXN-A"}}, level="ERROR")),
    ]
    _, _, pattern_result = _analyze(events)
    pattern = pattern_result.patterns[0]
    assert pattern.affected_flows == 1
    assert pattern.total_flows == 1


# ---------------------------------------------------------------------------
# Empty / degenerate input
# ---------------------------------------------------------------------------

def test_no_findings_returns_empty_patterns():
    events = [_clean_event("TXN-A", "2026-08-21T09:00:00Z"), _clean_event("TXN-B", "2026-08-21T09:01:00Z")]
    _, _, pattern_result = _analyze(events)
    assert pattern_result.patterns == []
    assert pattern_result.total_flows_analyzed == 2


def test_empty_population_returns_zero_denominator_gracefully():
    result_flows, events, findings = [], [], []
    from backend.analysis.pattern import analyze_recurring_patterns as arp

    pattern_result = arp(result_flows, events, findings)
    assert pattern_result.total_flows_analyzed == 0
    assert pattern_result.patterns == []
