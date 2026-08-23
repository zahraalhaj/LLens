"""
Tests for the Phase 9 data-quality and analytical-confidence monitoring
engine: backend/analysis/quality.py + backend/analysis/quality_schema.py.
"""
from backend.analysis.correlate import correlate_events
from backend.analysis.normalize import normalize_event
from backend.analysis.normalized_schema import LogFamily, NormalizedEvent, derive_tracker_type_and_phase
from backend.analysis.quality import (
    analyze_data_quality,
    check_field_consistency,
    compute_correlation_quality,
    scan_for_sensitive_data,
)
from backend.analysis.quality_schema import FieldCheckStatus, SensitiveDataCategory


def _canonical(source_system, event_id, ts, component, correlation_id, details, level="INFO", raw=None):
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
        "raw": raw or f"raw-{event_id}",
        "attributes": {"correlation_id": correlation_id, "details": details},
    }


def _ne(source_event_id, log_family=LogFamily.CARDINAL, tracker_no=None, **overrides):
    tracker_type, phase = derive_tracker_type_and_phase(tracker_no)
    defaults = dict(
        source_file="sample.log",
        log_family=log_family,
        event_no=1,
        raw_reference=f"raw-{source_event_id}",
        source_event_id=source_event_id,
        batch_id="batch-1",
        event_timestamp="2026-08-21T09:00:00Z",
        level="INFO",
        event_type="some_event",
        tracker_no=tracker_no,
        tracker_type=tracker_type,
        phase=phase,
    )
    defaults.update(overrides)
    return NormalizedEvent(**defaults)


# ---------------------------------------------------------------------------
# Fallback parsing detection (real parser vocabulary, verified in Phase 2)
# ---------------------------------------------------------------------------

def test_otp_processor_fallback_parsing_detected():
    e = normalize_event(
        _canonical("otp_online_processor", "e1", "2026-08-21T09:00:00Z", "sms_input_xml", None, {"tracker_no": "IA1", "parsed": {"parse_method": "regex_fallback"}, "record": {"tracker_no": "IA1"}})
    )
    assert e.used_fallback_parsing is True


def test_otp_processor_xml_parse_is_not_fallback():
    e = normalize_event(
        _canonical("otp_online_processor", "e1", "2026-08-21T09:00:00Z", "sms_input_xml", None, {"tracker_no": "IA1", "parsed": {"parse_method": "xml"}, "record": {"tracker_no": "IA1"}})
    )
    assert e.used_fallback_parsing is False


def test_debit_portal_fallback_parsing_detected():
    e = normalize_event(_canonical("debit_portal_log", "e1", "2026-08-21T09:00:00Z", "sms_input_xml", None, {"parsed": {"parse_method": "regex_xml_fallback"}}))
    assert e.used_fallback_parsing is True


def test_cardinal_fallback_parsing_detected_via_warnings():
    e = normalize_event(
        _canonical("cardinal_stepup_oob_log", "e1", "2026-08-21T09:00:00Z", "message", "TXN1", {"warnings": ["One or more JSON objects required fallback parsing"], "flow": {"transaction_id": "TXN1"}})
    )
    assert e.used_fallback_parsing is True


def test_vflex_fallback_parsing_detected_via_warnings():
    e = normalize_event(
        _canonical("vflex_transaction_log", "e1", "2026-08-21T09:00:00Z", "message", "TXN1", {"warnings": ["One or more JSON payloads required fallback parsing"], "transaction": {}})
    )
    assert e.used_fallback_parsing is True


# ---------------------------------------------------------------------------
# Parse quality counts
# ---------------------------------------------------------------------------

def test_parse_quality_counts_success_partial_failure():
    events = [
        _ne("a", parse_status="parsed"),
        _ne("b", parse_status="partial"),
        _ne("c", parse_status="failed"),
    ]
    result = correlate_events(events)
    qa = analyze_data_quality(events, result.flows)
    assert qa.scorecard.parse_quality.parse_success == 1
    assert qa.scorecard.parse_quality.partial_parsing == 1
    assert qa.scorecard.parse_quality.parse_failure == 1


# ---------------------------------------------------------------------------
# Field consistency: MISMATCH vs MISSING_VALUE vs CONSISTENT
# ---------------------------------------------------------------------------

def test_consistent_field_across_flow_events():
    events = [
        _ne("a", transaction_id="TXN1", issuer_id="ISS1"),
        _ne("b", transaction_id="TXN1", issuer_id="ISS1"),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    issuer_result = next(r for r in results if r.field_name == "issuer")
    assert issuer_result.status == FieldCheckStatus.CONSISTENT


def test_genuine_mismatch_detected():
    events = [
        _ne("a", transaction_id="TXN1", issuer_id="ISS-ALPHA"),
        _ne("b", transaction_id="TXN1", issuer_id="ISS-BETA"),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    issuer_result = next(r for r in results if r.field_name == "issuer")
    assert issuer_result.status == FieldCheckStatus.MISMATCH
    assert set(issuer_result.distinct_values) == {"ISS-ALPHA", "ISS-BETA"}


def test_null_versus_value_is_missing_value_not_mismatch():
    """One event reports an issuer, the other doesn't mention it at all --
    this must NEVER be flagged as a mismatch."""
    events = [
        _ne("a", transaction_id="TXN1", issuer_id="ISS1"),
        _ne("b", transaction_id="TXN1", issuer_id=None),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    issuer_result = next(r for r in results if r.field_name == "issuer")
    assert issuer_result.status == FieldCheckStatus.CONSISTENT  # the one non-null value stands uncontested
    assert issuer_result.status != FieldCheckStatus.MISMATCH


def test_field_entirely_absent_is_missing_value():
    events = [_ne("a", transaction_id="TXN1"), _ne("b", transaction_id="TXN1")]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    issuer_result = next(r for r in results if r.field_name == "issuer")
    assert issuer_result.status == FieldCheckStatus.MISSING_VALUE
    assert issuer_result.distinct_values == []


def test_amount_normalization_before_comparison():
    """42.5 and 42.50 must be treated as the SAME value after normalization."""
    events = [
        _ne("a", transaction_id="TXN1", amount=42.5),
        _ne("b", transaction_id="TXN1", amount=42.50),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    amount_result = next(r for r in results if r.field_name == "amount")
    assert amount_result.status == FieldCheckStatus.CONSISTENT


def test_currency_case_normalization_before_comparison():
    events = [
        _ne("a", transaction_id="TXN1", currency="usd"),
        _ne("b", transaction_id="TXN1", currency="USD"),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    currency_result = next(r for r in results if r.field_name == "currency")
    assert currency_result.status == FieldCheckStatus.CONSISTENT


def test_merchant_whitespace_and_case_normalization():
    events = [
        _ne("a", transaction_id="TXN1", merchant_name="  Acme   Corp "),
        _ne("b", transaction_id="TXN1", merchant_name="acme corp"),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    merchant_result = next(r for r in results if r.field_name == "merchant")
    assert merchant_result.status == FieldCheckStatus.CONSISTENT


def test_genuinely_different_amounts_are_a_mismatch():
    events = [
        _ne("a", transaction_id="TXN1", amount=42.50),
        _ne("b", transaction_id="TXN1", amount=99.99),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    amount_result = next(r for r in results if r.field_name == "amount")
    assert amount_result.status == FieldCheckStatus.MISMATCH


def test_masked_mobile_mismatch_still_detectable_through_masking():
    events = [
        _ne("a", transaction_id="TXN1", masked_mobile="*******67"),
        _ne("b", transaction_id="TXN1", masked_mobile="*******89"),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    mobile_result = next(r for r in results if r.field_name == "mobile")
    assert mobile_result.status == FieldCheckStatus.MISMATCH


def test_all_nine_required_fields_are_checked():
    events = [_ne("a", transaction_id="TXN1")]
    result = correlate_events(events)
    flow = result.flows[0]
    results = check_field_consistency(flow, events)
    checked = {r.field_name for r in results}
    assert checked == {"TransactionId", "StepupRequestId", "mobile", "email", "amount", "currency", "merchant", "card_last4", "issuer"}


# ---------------------------------------------------------------------------
# Flow classification
# ---------------------------------------------------------------------------

def test_flow_classification_counts_reuse_phase3_status():
    events = [
        _ne("a", transaction_id="TXN1"),
        _ne("b", transaction_id="TXN1"),  # PARTIAL (single family, no terminal status)
        _ne("c", transaction_id=None, tracker_no=None, correlation_id=None),  # UNCORRELATED
    ]
    result = correlate_events(events)
    qa = analyze_data_quality(events, result.flows)
    assert qa.scorecard.flow_classification_counts.get("PARTIAL") == 1
    assert qa.scorecard.flow_classification_counts.get("UNCORRELATED") == 1


# ---------------------------------------------------------------------------
# Correlation quality breakdown: by log family, source, confidence, parser
# ---------------------------------------------------------------------------

def test_correlation_quality_by_log_family_and_confidence():
    events = [
        _ne("a", log_family=LogFamily.CARDINAL, transaction_id="TXN1", source_file="cardinal.log"),
        _ne("b", log_family=LogFamily.CARDINAL, transaction_id="TXN1", source_file="cardinal.log"),
        _ne("c", log_family=LogFamily.VFLEX, correlation_id="CORR1", source_file="vflex.log"),
    ]
    result = correlate_events(events)
    breakdown = compute_correlation_quality(result.flows)
    assert breakdown.by_log_family["cardinal_stepup_oob_log"]["PARTIAL"] == 1
    assert "vflex_transaction_log" in breakdown.by_log_family
    assert breakdown.by_confidence.get("HIGH", 0) >= 1
    assert "1:1" in breakdown.parser_note or "parser" in breakdown.parser_note.lower()


def test_correlation_conflicts_and_low_confidence_counted():
    events = [
        _ne("a", transaction_id="TXN1", tracker_no="SU1"),
        _ne("b", transaction_id="TXN1", tracker_no="SU1"),
        _ne("c", transaction_id="TXN2", tracker_no="SU1"),
        _ne("d", transaction_id="TXN2", tracker_no="SU1"),
    ]
    result = correlate_events(events)
    assert len(result.conflicts) == 1
    qa = analyze_data_quality(events, result.flows, correlation_result=result)
    assert qa.scorecard.correlation_quality.correlation_conflicts == 1


# ---------------------------------------------------------------------------
# Sensitive-data detection -- must never leak the raw value
# ---------------------------------------------------------------------------

def test_pan_detected_with_safe_hint_not_raw_value():
    events = [_ne("a", raw_reference="Request body: CardNumber=4111111111111111 processed")]
    findings = scan_for_sensitive_data(events)
    pan_findings = [f for f in findings if f.category == SensitiveDataCategory.PAN]
    assert len(pan_findings) == 1
    assert pan_findings[0].safe_hint == "ends in 1111"
    dumped_finding = pan_findings[0].model_dump_json()
    assert "4111111111111111" not in dumped_finding


def test_otp_detected_and_value_never_stored():
    events = [_ne("a", raw_reference="OTP 482913 sent to customer")]
    findings = scan_for_sensitive_data(events)
    otp_findings = [f for f in findings if f.category == SensitiveDataCategory.OTP]
    assert len(otp_findings) == 1
    assert "482913" not in otp_findings[0].model_dump_json()
    assert otp_findings[0].safe_hint is None


def test_email_detected_and_never_stored_raw():
    events = [_ne("a", raw_reference="Message sent to jane.doe@example.com successfully")]
    findings = scan_for_sensitive_data(events)
    email_findings = [f for f in findings if f.category == SensitiveDataCategory.FULL_EMAIL]
    assert len(email_findings) == 1
    assert "jane.doe@example.com" not in email_findings[0].model_dump_json()


def test_secret_detected_and_never_stored_raw():
    events = [_ne("a", raw_reference='VerificationToken="s3cr3t-token-value-123"')]
    findings = scan_for_sensitive_data(events)
    secret_findings = [f for f in findings if f.category == SensitiveDataCategory.SECRET]
    assert len(secret_findings) == 1
    assert "s3cr3t-token-value-123" not in secret_findings[0].model_dump_json()


def test_full_mobile_number_detected():
    events = [_ne("a", raw_reference="Mobile: +1 555 123 4567 confirmed")]
    findings = scan_for_sensitive_data(events)
    mobile_findings = [f for f in findings if f.category == SensitiveDataCategory.FULL_MOBILE]
    assert len(mobile_findings) == 1
    assert "5551234567" not in mobile_findings[0].model_dump_json()
    assert "555 123 4567" not in mobile_findings[0].model_dump_json()


def test_clean_text_produces_no_findings():
    events = [_ne("a", raw_reference="Request received and queued for processing")]
    findings = scan_for_sensitive_data(events)
    assert findings == []


def test_protected_reference_is_deterministic_but_non_reversible():
    events_a = [_ne("a", raw_reference="CardNumber=4111111111111111")]
    events_b = [_ne("b", raw_reference="CardNumber=4111111111111111")]
    ref_a = scan_for_sensitive_data(events_a)[0].protected_reference
    ref_b = scan_for_sensitive_data(events_b)[0].protected_reference
    assert ref_a == ref_b  # same underlying value -> same reference, deterministic
    assert len(ref_a) == 16
    assert ref_a != "4111111111111111"


def test_full_analysis_result_never_contains_raw_sensitive_values():
    events = [
        _ne(
            "a",
            transaction_id="TXN1",
            raw_reference="Request body: CardNumber=4111111111111111 OTP 482913 to jane.doe@example.com Mobile +15551234567",
        )
    ]
    result = correlate_events(events)
    qa = analyze_data_quality(events, result.flows)
    dumped = qa.model_dump_json()
    assert "4111111111111111" not in dumped
    assert "482913" not in dumped
    assert "jane.doe@example.com" not in dumped
    assert "5551234567" not in dumped


# ---------------------------------------------------------------------------
# Evidence quality: missing identifiers/timestamps/merchant, unmatched/uncorrelated
# ---------------------------------------------------------------------------

def test_missing_identifier_and_uncorrelated_vs_unmatched_distinction():
    events = [
        _ne("no-id", tracker_no=None, correlation_id=None),  # no identifier at all
        _ne("has-id", transaction_id="TXN-ALONE"),  # has an identifier but matches nothing
    ]
    result = correlate_events(events)
    qa = analyze_data_quality(events, result.flows)
    assert qa.scorecard.evidence_quality.missing_identifiers == 1
    assert qa.scorecard.evidence_quality.unmatched_events == 1
    assert qa.scorecard.evidence_quality.uncorrelated_events == 2  # both are singleton/UNCORRELATED flows


def test_missing_timestamp_counted():
    events = [_ne("a", event_timestamp=None)]
    result = correlate_events(events)
    qa = analyze_data_quality(events, result.flows)
    assert qa.scorecard.evidence_quality.missing_timestamps == 1


def test_unknown_merchant_counted():
    events = [_ne("a", merchant_name=None), _ne("b", merchant_name="Acme")]
    result = correlate_events(events)
    qa = analyze_data_quality(events, result.flows)
    assert qa.scorecard.evidence_quality.unknown_merchant == 1


# ---------------------------------------------------------------------------
# Exception table
# ---------------------------------------------------------------------------

def test_exception_table_includes_parse_failures_and_mismatches():
    events = [
        _ne("a", parse_status="failed", failure_signature="x:y:z"),
        _ne("b", transaction_id="TXN1", issuer_id="ISS-ALPHA"),
        _ne("c", transaction_id="TXN1", issuer_id="ISS-BETA"),
    ]
    result = correlate_events(events)
    qa = analyze_data_quality(events, result.flows)
    categories = {e.category for e in qa.exception_table}
    assert "parse_failure" in categories
    assert "field_mismatch" in categories


# ---------------------------------------------------------------------------
# Overall score -- deterministic, transparent
# ---------------------------------------------------------------------------

def test_overall_score_is_deterministic_and_bounded():
    events = [_ne("a", transaction_id="TXN1"), _ne("b", transaction_id="TXN1")]
    result = correlate_events(events)
    qa1 = analyze_data_quality(events, result.flows)
    qa2 = analyze_data_quality(events, result.flows)
    assert qa1.scorecard.overall_score == qa2.scorecard.overall_score
    assert 0.0 <= qa1.scorecard.overall_score <= 100.0
    assert set(qa1.scorecard.score_breakdown.keys()) == {
        "parse_success_rate",
        "flow_completeness_rate",
        "field_consistency",
        "conflict_free_rate",
    }


def test_perfect_data_scores_high():
    """A single isolated event is never flow-COMPLETE by Phase 3's own
    design (COMPLETE requires >=2 log families and a terminal status) --
    "perfect data" here means a genuinely complete, conflict-free,
    consistent, cleanly-parsed cross-family flow."""
    events = [
        _ne("a", log_family=LogFamily.CARDINAL, transaction_id="TXN1", parse_status="parsed", terminal_status="OK"),
        _ne("b", log_family=LogFamily.NETCETERA_VPLUS, transaction_id="TXN1", parse_status="parsed", terminal_status="SUCCESS"),
    ]
    result = correlate_events(events)
    assert result.flows[0].correlation_status.value == "COMPLETE"
    qa = analyze_data_quality(events, result.flows)
    assert qa.scorecard.overall_score == 100.0


def test_empty_population_does_not_crash_and_returns_zero_totals():
    qa = analyze_data_quality([], [])
    assert qa.scorecard.total_events_analyzed == 0
    assert qa.scorecard.total_flows_analyzed == 0
    assert qa.scorecard.overall_score >= 0.0
