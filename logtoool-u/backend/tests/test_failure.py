"""
Tests for the Phase 6 deterministic failure analysis engine:
backend/analysis/failure.py + backend/analysis/failure_schema.py.
"""
from backend.analysis.correlate import correlate_events
from backend.analysis.dependency import compute_otp_handoff_chain
from backend.analysis.failure import analyze_failures, classify_event
from backend.analysis.failure_schema import Confidence, FailureSignature, RoutingArea, Severity
from backend.analysis.normalize import normalize_event
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent, derive_tracker_type_and_phase


def _ne(event_type, raw, level="ERROR", log_family=LogFamily.CARDINAL, tracker_no=None, **overrides):
    tracker_type, phase = derive_tracker_type_and_phase(tracker_no)
    defaults = dict(
        source_file="sample.log",
        log_family=log_family,
        event_no=1,
        raw_reference=raw,
        source_event_id="e1",
        batch_id="batch-1",
        event_timestamp="2026-08-21T09:00:00Z",
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
# Each known failure signature -- exact-rule tier
# ---------------------------------------------------------------------------

def test_v_plus_mq_timeout_exact_rule():
    result = classify_event(_ne("vplus_mq_timeout", "Error on StepupCall V+: MQ Timeout"))
    assert result == (FailureSignature.V_PLUS_MQ_TIMEOUT, Confidence.HIGH)


def test_invalid_stepup_id_exact_rule():
    result = classify_event(_ne("message", "Exception: Invalid StepupRequestId supplied", level="ERROR"))
    assert result == (FailureSignature.INVALID_STEPUP_ID, Confidence.HIGH)


def test_invalid_stepup_id_case_and_spacing_insensitive():
    result = classify_event(_ne("web_exception", "invalid STEPUP request id detected", level="ERROR"))
    assert result[0] == FailureSignature.INVALID_STEPUP_ID


def test_oob_http_error_exact_rule():
    result = classify_event(_ne("oob_http_error", "OOB Tracker ID: SU1 Error HTTP 503"))
    assert result == (FailureSignature.OOB_HTTP_ERROR, Confidence.HIGH)


def test_oob_business_error_exact_rule_status_api_error():
    result = classify_event(_ne("oob_status_api_error", "InvokeOobApiGet Error Code: 500"))
    assert result == (FailureSignature.OOB_BUSINESS_ERROR, Confidence.HIGH)


def test_oob_business_error_exact_rule_validate_exception():
    result = classify_event(_ne("oob_validate_exception", "Exception during Validate API call"))
    assert result == (FailureSignature.OOB_BUSINESS_ERROR, Confidence.HIGH)


def test_empty_response_exact_rule_event_type():
    result = classify_event(_ne("oob_empty_status_response", "OOB status API returned null or empty"))
    assert result == (FailureSignature.EMPTY_RESPONSE, Confidence.HIGH)


def test_empty_response_exact_rule_text_pattern_other_family():
    """The text-pattern rule catches an equivalent phrase from a family
    with no dedicated event_type for it."""
    result = classify_event(_ne("bank_api_error_response", "Response returned empty from bank host", log_family=LogFamily.VFLEX))
    assert result[0] == FailureSignature.EMPTY_RESPONSE


def test_host_response_code_failure_from_http_status_field():
    result = classify_event(_ne("some_event", "generic text", http_status="503", level="ERROR"))
    assert result == (FailureSignature.HOST_RESPONSE_CODE_FAILURE, Confidence.HIGH)


def test_host_response_code_failure_ignores_2xx_status():
    result = classify_event(_ne("some_event", "all good", http_status="200", level="INFO"))
    assert result is None


def test_parser_failure_exact_rule():
    result = classify_event(_ne("other", "unclassified junk", level="WARN", parse_status="failed"))
    assert result == (FailureSignature.PARSER_FAILURE, Confidence.HIGH)


# ---------------------------------------------------------------------------
# QUEUE_GAP / MISSING_PROCESSOR_RECEIPT / CORRELATION_CONFLICT -- derived
# from structured Phase 3/5 output, not text classification
# ---------------------------------------------------------------------------

def test_missing_processor_receipt_from_queue_handoff():
    events = [
        _ne("otp_input", "otp input", level="INFO", tracker_no="IA1", source_event_id="a1", event_timestamp="2026-08-21T09:00:00Z"),
        _ne("otp_queue", "otp queued", level="INFO", tracker_no="IA1", source_event_id="a2", event_timestamp="2026-08-21T09:00:01Z"),
    ]
    handoff = compute_otp_handoff_chain(events)
    analysis = analyze_failures(events, queue_handoff=handoff)

    finding = next(f for f in analysis.findings if f.finding_type == FailureSignature.MISSING_PROCESSOR_RECEIPT)
    assert finding.confidence == Confidence.HIGH
    assert finding.suggested_route == RoutingArea.MESSAGING_QUEUE
    assert "IA1" in finding.affected_flow_ids
    assert analysis.signature_flow_counts["MISSING_PROCESSOR_RECEIPT"] == 1


def test_queue_gap_from_orphan_processor_message():
    events = [
        _ne(
            "msg_received_sms_xml", "processor received", level="INFO", tracker_no="IA9",
            log_family=LogFamily.OTP_PROCESSOR, source_event_id="p1", event_timestamp="2026-08-21T09:00:00Z",
        ),
    ]
    handoff = compute_otp_handoff_chain(events)
    analysis = analyze_failures(events, queue_handoff=handoff)

    finding = next(f for f in analysis.findings if f.finding_type == FailureSignature.QUEUE_GAP)
    assert "IA9" in finding.affected_flow_ids
    assert analysis.signature_flow_counts["QUEUE_GAP"] == 1


def test_correlation_conflict_finding_from_phase3_conflict():
    events = [
        normalize_event(_canonical("cardinal_stepup_oob_log", "a", "2026-08-21T09:00:00Z", "vplus_input", "TXN1", {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "b", "2026-08-21T09:00:01Z", "vplus_input", "TXN1", {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "c", "2026-08-21T09:05:00Z", "vplus_input", "TXN2", {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "d", "2026-08-21T09:05:01Z", "vplus_input", "TXN2", {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}})),
    ]
    result = correlate_events(events)
    assert len(result.conflicts) == 1

    analysis = analyze_failures(events, flows=result.flows, conflicts=result.conflicts, queue_handoff=compute_otp_handoff_chain(events))
    finding = next(f for f in analysis.findings if f.finding_type == FailureSignature.CORRELATION_CONFLICT)
    assert finding.confidence == Confidence.HIGH
    assert finding.suggested_route == RoutingArea.APPLICATION_SUPPORT
    assert set(finding.evidence_event_ids) == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# Unknown errors
# ---------------------------------------------------------------------------

def test_unknown_error_when_no_rule_or_keyword_matches():
    result = classify_event(_ne("error", "Something completely unrelated blew up spectacularly"))
    assert result == (FailureSignature.UNKNOWN_ERROR, Confidence.LOW)


def test_non_failure_event_returns_none_not_unknown_error():
    """A routine INFO-level event must never be classified at all -- not
    even as UNKNOWN_ERROR -- just because it mentions an unrelated word."""
    result = classify_event(_ne("message", "normal info line mentioning an empty shopping basket", level="INFO"))
    assert result is None


def test_unknown_error_finding_routes_to_application_support():
    events = [_ne("error", "Something completely unrelated blew up", source_event_id="z1")]
    analysis = analyze_failures(events)
    finding = next(f for f in analysis.findings if f.finding_type == FailureSignature.UNKNOWN_ERROR)
    assert finding.suggested_route == RoutingArea.APPLICATION_SUPPORT
    assert finding.confidence == Confidence.LOW


# ---------------------------------------------------------------------------
# Tier 2: normalize -> tokenize -> classify fallback
# ---------------------------------------------------------------------------

def test_fallback_keyword_match_is_medium_confidence():
    """No exact event_type/regex/field rule fires, but the normalized
    token set still matches a known signature's keyword set."""
    result = classify_event(_ne("generic_error", "The mq broker reported a timeout condition on submit"))
    assert result == (FailureSignature.V_PLUS_MQ_TIMEOUT, Confidence.MEDIUM)


def test_fallback_tokenization_ignores_punctuation_and_case():
    result = classify_event(_ne("generic_error", "MQ-TIMEOUT!!! (retrying...)"))
    assert result[0] == FailureSignature.V_PLUS_MQ_TIMEOUT
    assert result[1] == Confidence.MEDIUM


def test_exact_rule_always_wins_over_fallback_keywords():
    """Text that would ALSO match a fallback keyword set must still be
    classified via the higher-priority exact event_type rule, at HIGH not
    MEDIUM confidence."""
    result = classify_event(_ne("vplus_mq_timeout", "mq timeout retry pending"))
    assert result == (FailureSignature.V_PLUS_MQ_TIMEOUT, Confidence.HIGH)


# ---------------------------------------------------------------------------
# Finding shape -- every required field present
# ---------------------------------------------------------------------------

def test_finding_contains_all_required_fields():
    events = [_ne("vplus_mq_timeout", "Error on StepupCall V+: MQ Timeout", source_event_id="e1", source_file="cardinal.log")]
    analysis = analyze_failures(events)
    finding = analysis.findings[0]

    assert finding.finding_type == FailureSignature.V_PLUS_MQ_TIMEOUT
    assert finding.severity == Severity.HIGH
    assert isinstance(finding.statement, str) and finding.statement
    assert finding.confidence == Confidence.HIGH
    assert finding.evidence_event_ids == ["e1"]
    assert finding.source_files == ["cardinal.log"]
    assert finding.suggested_route == RoutingArea.MIDDLEWARE_VPLUS


def test_statement_is_deterministic_template_not_free_text():
    """Same input -> byte-identical statement across repeated calls (no
    randomness, no external generation)."""
    events = [_ne("vplus_mq_timeout", "Error on StepupCall V+: MQ Timeout")]
    a1 = analyze_failures(events).findings[0].statement
    a2 = analyze_failures(events).findings[0].statement
    assert a1 == a2


def test_routing_areas_are_the_fixed_team_set_not_arbitrary_names():
    """Routing must resolve to one of the seven defined team/department
    labels -- a closed, reviewable set -- never an arbitrary string (e.g.
    an email address or a person's name) that could slip in as blame."""
    expected = {
        "Application Support",
        "Middleware / V+",
        "Issuer Host",
        "Bank API",
        "OOB Integration",
        "Messaging / Queue",
        "Parser / Data Quality",
    }
    assert {area.value for area in RoutingArea} == expected
    for area in RoutingArea:
        assert "@" not in area.value


def test_finding_never_carries_an_assignee_or_owner_person_field():
    """The Finding schema itself must have no field for an individual --
    only suggested_route (a team)."""
    from backend.analysis.failure_schema import Finding

    field_names = set(Finding.model_fields.keys())
    assert field_names.isdisjoint({"assignee", "owner", "assigned_to", "engineer", "blame"})


# ---------------------------------------------------------------------------
# Aggregation: count affected flows, not raw error lines
# ---------------------------------------------------------------------------

def test_repeated_error_lines_in_same_flow_count_once():
    events = [
        normalize_event(_canonical("cardinal_stepup_oob_log", "e1", "2026-08-21T09:00:00Z", "vplus_input", None, {"flow": {"transaction_id": "TXN1"}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "e2", "2026-08-21T09:00:02Z", "vplus_mq_timeout", None, {"flow": {"transaction_id": "TXN1"}}, level="ERROR")),
        normalize_event(_canonical("cardinal_stepup_oob_log", "e3", "2026-08-21T09:00:04Z", "vplus_mq_timeout", None, {"flow": {"transaction_id": "TXN1"}}, level="ERROR")),
        normalize_event(_canonical("cardinal_stepup_oob_log", "e4", "2026-08-21T09:00:06Z", "vplus_mq_timeout", None, {"flow": {"transaction_id": "TXN1"}}, level="ERROR")),
    ]
    result = correlate_events(events)
    assert len(result.flows) == 1

    analysis = analyze_failures(events, flows=result.flows)
    assert analysis.signature_flow_counts["V_PLUS_MQ_TIMEOUT"] == 1  # NOT 3
    finding = next(f for f in analysis.findings if f.finding_type == FailureSignature.V_PLUS_MQ_TIMEOUT)
    assert finding.occurrence_count == 3  # raw evidence count is still visible, just not the headline metric
    assert set(finding.evidence_event_ids) == {"e2", "e3", "e4"}


def test_same_failure_across_two_flows_counts_as_two():
    events = [
        normalize_event(_canonical("cardinal_stepup_oob_log", "a1", "2026-08-21T09:00:00Z", "vplus_input", None, {"flow": {"transaction_id": "TXN1"}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "a2", "2026-08-21T09:00:02Z", "vplus_mq_timeout", None, {"flow": {"transaction_id": "TXN1"}}, level="ERROR")),
        normalize_event(_canonical("cardinal_stepup_oob_log", "b1", "2026-08-21T10:00:00Z", "vplus_input", None, {"flow": {"transaction_id": "TXN2"}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "b2", "2026-08-21T10:00:02Z", "vplus_mq_timeout", None, {"flow": {"transaction_id": "TXN2"}}, level="ERROR")),
    ]
    result = correlate_events(events)
    assert len(result.flows) == 2

    analysis = analyze_failures(events, flows=result.flows)
    assert analysis.signature_flow_counts["V_PLUS_MQ_TIMEOUT"] == 2
    assert analysis.total_affected_flows == 2
    assert len([f for f in analysis.findings if f.finding_type == FailureSignature.V_PLUS_MQ_TIMEOUT]) == 2


def test_without_flows_each_event_is_its_own_aggregation_unit():
    """Honest degradation: no flow context supplied -- can't collapse by
    transaction, so each event is counted individually rather than
    silently guessed at."""
    events = [
        _ne("vplus_mq_timeout", "timeout 1", source_event_id="x1"),
        _ne("vplus_mq_timeout", "timeout 2", source_event_id="x2"),
    ]
    analysis = analyze_failures(events)  # no flows passed
    assert analysis.signature_flow_counts["V_PLUS_MQ_TIMEOUT"] == 2
    assert len([f for f in analysis.findings if f.finding_type == FailureSignature.V_PLUS_MQ_TIMEOUT]) == 2


def test_different_signatures_in_same_flow_produce_separate_findings():
    events = [
        _ne("vplus_mq_timeout", "timeout", source_event_id="x1", tracker_no="SU1"),
        _ne("oob_http_error", "http error", source_event_id="x2", tracker_no="SU1"),
    ]
    analysis = analyze_failures(events)
    signatures = {f.finding_type for f in analysis.findings}
    assert FailureSignature.V_PLUS_MQ_TIMEOUT in signatures
    assert FailureSignature.OOB_HTTP_ERROR in signatures
    assert len(analysis.findings) == 2


# ---------------------------------------------------------------------------
# Routing precision
# ---------------------------------------------------------------------------

def test_bank_api_host_failure_routes_to_bank_api_not_generic_issuer_host():
    events = [_ne("bank_api_error_response", "bad response", log_family=LogFamily.VFLEX, http_status="502")]
    analysis = analyze_failures(events)
    finding = next(f for f in analysis.findings if f.finding_type == FailureSignature.HOST_RESPONSE_CODE_FAILURE)
    assert finding.suggested_route == RoutingArea.BANK_API


def test_non_vflex_host_failure_routes_to_generic_issuer_host():
    events = [_ne("oob_status_poll", "status check", log_family=LogFamily.CARDINAL, http_status="500")]
    analysis = analyze_failures(events)
    finding = next(f for f in analysis.findings if f.finding_type == FailureSignature.HOST_RESPONSE_CODE_FAILURE)
    assert finding.suggested_route == RoutingArea.ISSUER_HOST


def test_parser_failure_routes_to_parser_data_quality():
    events = [_ne("other", "junk", level="WARN", parse_status="failed")]
    analysis = analyze_failures(events)
    finding = next(f for f in analysis.findings if f.finding_type == FailureSignature.PARSER_FAILURE)
    assert finding.suggested_route == RoutingArea.PARSER_DATA_QUALITY
