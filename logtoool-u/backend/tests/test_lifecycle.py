"""
Tests for the Phase 4 deterministic lifecycle reconstruction engine:
backend/analysis/lifecycle.py + backend/analysis/lifecycle_schema.py.

Two layers of test data, same convention as test_correlate.py:
- _ne(...) builds NormalizedEvent instances directly with an explicit
  event_type -- precise, decoupled from Phase 2 mapping details, used for
  the required complete/incomplete/failed/pending/rejected scenario tests.
- The realistic end-to-end section at the bottom goes through
  normalize_event() + correlate_events() on raw canonical-event dicts,
  proving Phase 2 -> Phase 3 -> Phase 4 connect correctly.
"""
from backend.analysis.correlate import correlate_events
from backend.analysis.lifecycle import (
    OOB_TEMPLATE_STAGES,
    OTP_TEMPLATE_STAGES,
    reconstruct_lifecycle,
    reconstruct_lifecycles,
)
from backend.analysis.lifecycle_schema import AuthTemplate, TerminalStatus
from backend.analysis.normalize import normalize_event
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent


def _ne(event_no, source_event_id, event_type, ts, level="INFO", log_family=LogFamily.CARDINAL, **overrides):
    defaults = dict(
        source_file="cardinal.log",
        log_family=log_family,
        event_no=event_no,
        raw_reference=f"raw-{source_event_id}",
        source_event_id=source_event_id,
        batch_id="batch-1",
        event_timestamp=ts,
        level=level,
        event_type=event_type,
        transaction_id="TXN-1",
    )
    defaults.update(overrides)
    return NormalizedEvent(**defaults)


def _flow_from(events):
    result = correlate_events(events)
    assert len(result.flows) == 1, "test data must correlate into exactly one flow"
    return result.flows[0]


def _lifecycle(events):
    return reconstruct_lifecycle(_flow_from(events), events)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def test_otp_template_excludes_oob_only_stages():
    assert "OOB_INITIATED" not in OTP_TEMPLATE_STAGES
    assert "PROCESSOR_RECEIVED" not in OTP_TEMPLATE_STAGES
    assert "DOWNSTREAM_QUEUE_SELECTED" not in OTP_TEMPLATE_STAGES
    assert "OTP_GENERATED" in OTP_TEMPLATE_STAGES
    assert "APPLICATION_QUEUE_CONFIRMED" in OTP_TEMPLATE_STAGES


def test_oob_template_excludes_otp_only_stages():
    assert "OTP_GENERATED" not in OOB_TEMPLATE_STAGES
    assert "APPLICATION_QUEUE_CONFIRMED" not in OOB_TEMPLATE_STAGES
    assert "OOB_INITIATED" in OOB_TEMPLATE_STAGES
    assert "PROCESSOR_RECEIVED" in OOB_TEMPLATE_STAGES


def test_both_templates_preserve_master_relative_order():
    from backend.analysis.lifecycle import MASTER_STAGE_ORDER

    master_index = {stage: i for i, stage in enumerate(MASTER_STAGE_ORDER)}
    for template in (OTP_TEMPLATE_STAGES, OOB_TEMPLATE_STAGES):
        indices = [master_index[s] for s in template]
        assert indices == sorted(indices)


# ---------------------------------------------------------------------------
# Complete OTP
# ---------------------------------------------------------------------------

def test_complete_otp_flow():
    events = [
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "otp_input", "2026-08-21T09:00:02Z"),
        _ne(3, "c", "otp_queue", "2026-08-21T09:00:04Z"),
        _ne(4, "d", "otp_success", "2026-08-21T09:00:30Z"),
        _ne(5, "e", "cardinal_validate_response", "2026-08-21T09:00:31Z", authentication_method="OTP", terminal_status="SUCCESS"),
    ]
    lc = _lifecycle(events)

    assert lc.auth_template == AuthTemplate.OTP
    assert lc.last_confirmed_stage == "AUTH_COMPLETED"
    assert lc.terminal_status == TerminalStatus.SUCCESS
    assert lc.failure_boundary is None
    assert lc.expected_next_stage is None
    assert lc.missing_next_stage is None
    assert lc.first_event.source_event_id == "a"
    assert lc.last_event.source_event_id == "e"
    assert len(lc.timeline) == 5
    assert len(lc.stage_durations) >= 1
    # confirmed order must be chronological and match the OTP template order
    confirmed_stages_in_order = [s.to_stage for s in lc.stage_durations]
    assert confirmed_stages_in_order == sorted(
        confirmed_stages_in_order, key=lambda s: OTP_TEMPLATE_STAGES.index(s)
    )


# ---------------------------------------------------------------------------
# Incomplete OTP
# ---------------------------------------------------------------------------

def test_incomplete_otp_flow_no_error():
    events = [
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "otp_input", "2026-08-21T09:00:02Z"),
    ]
    lc = _lifecycle(events)

    assert lc.auth_template == AuthTemplate.OTP
    assert lc.last_confirmed_stage == "OTP_GENERATED"
    assert lc.terminal_status == TerminalStatus.INCOMPLETE
    assert lc.failure_boundary is None
    assert lc.expected_next_stage == "APPLICATION_QUEUE_CONFIRMED"
    assert lc.missing_next_stage == "APPLICATION_QUEUE_CONFIRMED: not found in captured logs"


# ---------------------------------------------------------------------------
# Failed OTP
# ---------------------------------------------------------------------------

def test_failed_otp_flow_technical_error():
    events = [
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "otp_input", "2026-08-21T09:00:02Z"),
        _ne(3, "c", "vplus_mq_timeout", "2026-08-21T09:00:04Z", level="ERROR", failure_signature="cardinal:vplus_mq_timeout:mq_timeout"),
    ]
    lc = _lifecycle(events)

    assert lc.terminal_status == TerminalStatus.FAILED
    assert lc.last_confirmed_stage == "OTP_GENERATED"  # vplus_mq_timeout itself isn't a mapped stage
    assert lc.failure_boundary is not None
    assert lc.failure_boundary.after_stage == "OTP_GENERATED"
    assert lc.failure_boundary.at_event_id == "c"
    assert lc.failure_boundary.level == "ERROR"
    assert lc.failure_boundary.reason == "cardinal:vplus_mq_timeout:mq_timeout"


def test_failed_otp_uses_generic_reason_when_no_failure_signature():
    events = [
        _ne(1, "a", "otp_input", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "web_exception", "2026-08-21T09:00:05Z", level="ERROR"),
    ]
    lc = _lifecycle(events)
    assert lc.terminal_status == TerminalStatus.FAILED
    assert "ERROR" in (lc.failure_boundary.reason or "") or lc.failure_boundary.reason == "Terminal event recorded at ERROR/CRITICAL level."


# ---------------------------------------------------------------------------
# Complete OOB
# ---------------------------------------------------------------------------

def test_complete_oob_flow():
    events = [
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "vplus_input", "2026-08-21T09:00:02Z"),
        _ne(3, "c", "oob_authenticate_api", "2026-08-21T09:00:05Z"),
        _ne(4, "d", "oob_status_poll", "2026-08-21T09:00:10Z", oob_status="PENDING"),
        _ne(5, "e", "oob_validate_api", "2026-08-21T09:00:20Z", oob_status="SUCCESS"),
        _ne(6, "f", "cardinal_validate_response", "2026-08-21T09:00:21Z", terminal_status="SUCCESS"),
    ]
    lc = _lifecycle(events)

    assert lc.auth_template == AuthTemplate.OOB
    assert lc.last_confirmed_stage == "AUTH_COMPLETED"
    assert lc.terminal_status == TerminalStatus.SUCCESS
    assert lc.failure_boundary is None


# ---------------------------------------------------------------------------
# Pending OOB
# ---------------------------------------------------------------------------

def test_pending_oob_flow_is_not_a_failure():
    events = [
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "vplus_input", "2026-08-21T09:00:02Z"),
        _ne(3, "c", "oob_authenticate_api", "2026-08-21T09:00:05Z"),
        _ne(4, "d", "oob_status_poll", "2026-08-21T09:00:10Z", oob_status="PENDING"),
    ]
    lc = _lifecycle(events)

    assert lc.auth_template == AuthTemplate.OOB
    assert lc.last_confirmed_stage == "CUSTOMER_RESPONSE_PENDING"
    assert lc.terminal_status == TerminalStatus.PENDING_AT_LOG_END
    assert lc.terminal_status != TerminalStatus.FAILED
    assert lc.failure_boundary is None


def test_pending_oob_with_multiple_poll_events_still_pending():
    events = [
        _ne(1, "a", "oob_authenticate_api", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "oob_status_poll", "2026-08-21T09:00:05Z", oob_status="PENDING"),
        _ne(3, "c", "oob_status_poll", "2026-08-21T09:00:10Z", oob_status="PENDING"),
        _ne(4, "d", "oob_status_poll", "2026-08-21T09:00:15Z", oob_status="PENDING"),
    ]
    lc = _lifecycle(events)
    assert lc.terminal_status == TerminalStatus.PENDING_AT_LOG_END


# ---------------------------------------------------------------------------
# Rejected OOB
# ---------------------------------------------------------------------------

def test_rejected_oob_flow_is_failed_via_business_outcome_not_technical_error():
    events = [
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "vplus_input", "2026-08-21T09:00:02Z"),
        _ne(3, "c", "oob_authenticate_api", "2026-08-21T09:00:05Z"),
        _ne(4, "d", "oob_status_poll", "2026-08-21T09:00:10Z", oob_status="PENDING"),
        _ne(5, "e", "oob_validate_api", "2026-08-21T09:00:20Z", oob_status="REJECTED"),  # INFO level -- not a technical error
    ]
    lc = _lifecycle(events)

    assert lc.terminal_status == TerminalStatus.FAILED
    assert lc.last_confirmed_stage == "CUSTOMER_RESPONSE_RECEIVED"
    assert lc.failure_boundary is not None
    assert lc.failure_boundary.at_event_id == "e"
    assert "rejected" in lc.failure_boundary.reason.lower() or "declined" in lc.failure_boundary.reason.lower() or "failed" in lc.failure_boundary.reason.lower()
    # confirm this was NOT classified via the technical-error path
    assert lc.failure_boundary.level != "ERROR"


def test_declined_stepup_status_also_counts_as_rejected():
    events = [
        _ne(1, "a", "vplus_input", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "oob_authenticate_api", "2026-08-21T09:00:02Z"),
        _ne(3, "c", "oob_validate_api", "2026-08-21T09:00:05Z", stepup_status="DECLINED"),
    ]
    lc = _lifecycle(events)
    assert lc.terminal_status == TerminalStatus.FAILED


# ---------------------------------------------------------------------------
# Partial logs
# ---------------------------------------------------------------------------

def test_partial_logs_single_family_marks_unreachable_stages_not_observable():
    """Only Debit Portal events available -- stages only ever produced by
    other families (e.g. Cardinal's OOB_INITIATED, VFlex's CARD_LOOKUP_*)
    must be NOT_OBSERVABLE, not MISSING."""
    events = [
        _ne(1, "a", "debit_request_json", "2026-08-21T09:00:00Z", log_family=LogFamily.DEBIT_PORTAL, source_file="debit.log", authentication_method="OTP"),
        _ne(2, "b", "queue", "2026-08-21T09:00:02Z", log_family=LogFamily.DEBIT_PORTAL, source_file="debit.log", authentication_method="OTP"),
    ]
    lc = _lifecycle(events)

    assert lc.auth_template == AuthTemplate.OTP
    assert "CARD_LOOKUP_SENT" in lc.not_observable_stages
    assert "CARD_LOOKUP_COMPLETED" in lc.not_observable_stages
    assert "CHALLENGE_SELECTED" in lc.not_observable_stages  # only Cardinal/Netcetera ever produce this
    assert "CARD_LOOKUP_SENT" not in lc.missing_stages
    assert lc.missing_next_stage.endswith("NOT_OBSERVABLE") or "APPLICATION_QUEUE_CONFIRMED" == lc.last_confirmed_stage


def test_partial_logs_family_present_but_stage_never_fired_is_missing_not_not_observable():
    """Cardinal IS present in this flow (so CHALLENGE_SELECTED is
    structurally observable) but the flow's evidence simply never shows
    it -- MISSING, not NOT_OBSERVABLE."""
    events = [
        _ne(1, "a", "otp_input", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "otp_queue", "2026-08-21T09:00:02Z"),
    ]
    lc = _lifecycle(events)
    assert "CHALLENGE_SELECTED" in lc.missing_stages
    assert "CHALLENGE_SELECTED" not in lc.not_observable_stages


def test_no_identifiable_auth_method_yields_undetermined_template():
    """No structural OTP/OOB signal at all -- neither template applies,
    lifecycle analysis degrades gracefully instead of guessing."""
    events = [_ne(1, "a", "message", "2026-08-21T09:00:00Z")]
    lc = _lifecycle(events)

    assert lc.auth_template is None
    assert lc.terminal_status == TerminalStatus.UNDETERMINED
    assert lc.missing_stages == []
    assert lc.not_observable_stages == []
    assert lc.first_event is not None and lc.last_event is not None


# ---------------------------------------------------------------------------
# Sorting / tie-breaking
# ---------------------------------------------------------------------------

def test_events_sorted_by_timestamp_then_event_no_tiebreak():
    events = [
        _ne(3, "c", "otp_success", "2026-08-21T09:00:00Z"),  # same ts as a/b, higher event_no
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "otp_input", "2026-08-21T09:00:00Z"),
    ]
    lc = _lifecycle(events)
    assert [e.source_event_id for e in lc.timeline] == ["a", "b", "c"]


def test_out_of_order_input_list_still_produces_chronological_timeline():
    events = [
        _ne(4, "d", "otp_success", "2026-08-21T09:00:30Z"),
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(3, "c", "otp_queue", "2026-08-21T09:00:04Z"),
        _ne(2, "b", "otp_input", "2026-08-21T09:00:02Z"),
    ]
    lc = _lifecycle(events)
    assert [e.source_event_id for e in lc.timeline] == ["a", "b", "c", "d"]


# ---------------------------------------------------------------------------
# Duplicate exclusion from timeline
# ---------------------------------------------------------------------------

def test_exact_duplicate_events_appear_once_in_timeline():
    events = [
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z", raw_reference="identical text"),
        _ne(1, "a-dup", "request_body", "2026-08-21T09:00:00Z", raw_reference="identical text"),
        _ne(2, "b", "otp_input", "2026-08-21T09:00:02Z"),
    ]
    flow = _flow_from(events)
    assert "a-dup" in flow.duplicate_event_ids
    lc = reconstruct_lifecycle(flow, events)
    assert [e.source_event_id for e in lc.timeline] == ["a", "b"]


# ---------------------------------------------------------------------------
# Empty / degenerate input
# ---------------------------------------------------------------------------

def test_empty_event_list_returns_minimal_lifecycle():
    from backend.analysis.correlation_schema import CorrelatedFlow, CorrelationStatus

    empty_flow = CorrelatedFlow(flow_id="flow:empty", correlation_status=CorrelationStatus.UNCORRELATED)
    lc = reconstruct_lifecycle(empty_flow, [])
    assert lc.first_event is None
    assert lc.last_event is None
    assert lc.timeline == []
    assert lc.terminal_status == TerminalStatus.UNDETERMINED


def test_reconstruct_lifecycles_batch_matches_singular_calls():
    events_a = [_ne(1, "a", "request_body", "2026-08-21T09:00:00Z", transaction_id="TXN-A")]
    events_b = [_ne(1, "x", "request_body", "2026-08-21T10:00:00Z", transaction_id="TXN-B")]
    all_events = events_a + events_b
    result = correlate_events(all_events)
    assert len(result.flows) == 2

    batch = reconstruct_lifecycles(result.flows, all_events)
    singular = [reconstruct_lifecycle(f, all_events) for f in result.flows]
    assert [lc.flow_id for lc in batch] == [lc.flow_id for lc in singular]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_lifecycle_reconstruction_is_deterministic():
    events = [
        _ne(1, "a", "request_body", "2026-08-21T09:00:00Z"),
        _ne(2, "b", "otp_input", "2026-08-21T09:00:02Z"),
        _ne(3, "c", "otp_success", "2026-08-21T09:00:30Z"),
    ]
    flow = _flow_from(events)
    lc1 = reconstruct_lifecycle(flow, events)
    lc2 = reconstruct_lifecycle(flow, events)
    assert lc1.model_dump() == lc2.model_dump()


# ---------------------------------------------------------------------------
# Realistic end-to-end: Phase 2 normalize_event() -> Phase 3 correlate_events() -> Phase 4 reconstruct_lifecycle()
# ---------------------------------------------------------------------------

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


def test_realistic_cross_family_otp_flow_cardinal_and_otp_processor():
    """Cardinal drives the OTP challenge; a separate OTP Online Processor
    log shows the actual SMS delivery/success for the same tracker --
    correlated via tracker_no, then reconstructed as one lifecycle."""
    events = [
        normalize_event(
            _canonical(
                "cardinal_stepup_oob_log", "card-1", "2026-08-21T09:00:00Z", "request_body", "TXN-X",
                {"flow": {"transaction_id": "TXN-X", "trackers": ["IA8001"], "authentication": {"type": "OTP"}}},
            )
        ),
        normalize_event(
            _canonical(
                "cardinal_stepup_oob_log", "card-2", "2026-08-21T09:00:02Z", "otp_input", "TXN-X",
                {"flow": {"transaction_id": "TXN-X", "trackers": ["IA8001"], "authentication": {"type": "OTP"}}},
            )
        ),
        normalize_event(
            _canonical(
                "cardinal_stepup_oob_log", "card-3", "2026-08-21T09:00:30Z", "otp_success", "TXN-X",
                {"flow": {"transaction_id": "TXN-X", "trackers": ["IA8001"], "authentication": {"type": "OTP", "status": "SUCCESS"}}},
            )
        ),
        normalize_event(
            _canonical(
                "cardinal_stepup_oob_log", "card-4", "2026-08-21T09:00:31Z", "cardinal_validate_response", "TXN-X",
                {"flow": {"transaction_id": "TXN-X", "authentication": {"type": "OTP", "status": "SUCCESS"}, "integrity_status": "OK"}},
            )
        ),
    ]
    result = correlate_events(events)
    assert len(result.flows) == 1
    lc = reconstruct_lifecycle(result.flows[0], events)

    assert lc.auth_template == AuthTemplate.OTP
    assert lc.terminal_status == TerminalStatus.SUCCESS
    assert lc.last_confirmed_stage == "AUTH_COMPLETED"
    assert len(lc.timeline) == 4


def test_realistic_vflex_card_lookup_stages_observable():
    """VFlex is the only family that logs an explicit bank-data (card)
    lookup -- CARD_LOOKUP_SENT/COMPLETED must be observable and confirmed
    here, unlike the Cardinal-only partial-log test above."""
    otp_marker = {"transaction": {"otp": {"channel": "SMS"}}}
    events = [
        normalize_event(_canonical("vflex_transaction_log", "vf-1", "2026-08-21T09:00:00Z", "vf_input", "CORR-1", otp_marker)),
        normalize_event(_canonical("vflex_transaction_log", "vf-2", "2026-08-21T09:00:02Z", "bank_request", "CORR-1", otp_marker)),
        normalize_event(_canonical("vflex_transaction_log", "vf-3", "2026-08-21T09:00:04Z", "bank_api_success_response", "CORR-1", otp_marker)),
    ]
    result = correlate_events(events)
    lc = reconstruct_lifecycle(result.flows[0], events)

    assert lc.last_confirmed_stage == "CARD_LOOKUP_COMPLETED"
    assert "CARD_LOOKUP_SENT" not in lc.not_observable_stages
    assert "CARD_LOOKUP_COMPLETED" not in lc.not_observable_stages
