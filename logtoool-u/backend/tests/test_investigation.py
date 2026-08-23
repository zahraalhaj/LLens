"""
Tests for the Phase 7 AI investigation/explanation layer:
backend/analysis/investigation.py + backend/analysis/investigation_schema.py
+ backend/llm/investigate.py.

All LLM calls are mocked (same convention as backend/tests/test_llm.py) --
these tests verify the DETERMINISTIC scaffolding and the validation layer
that keeps the AI from making unsupported conclusions, never a live model.
"""
import json

from backend.analysis.correlate import correlate_events
from backend.analysis.failure import analyze_failures
from backend.analysis.investigation import build_investigation_case, find_similar_incidents
from backend.analysis.lifecycle import reconstruct_lifecycle
from backend.analysis.normalize import normalize_event
from backend.llm.client import OllamaClient
from backend.llm.investigate import InvestigationAssistant


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


def _failed_otp_case():
    events = [
        normalize_event(
            _canonical(
                "cardinal_stepup_oob_log", "a1", "2026-08-21T09:00:00Z", "request_body", "TXN-1",
                {"flow": {"transaction_id": "TXN-1", "authentication": {"type": "OTP"}, "merchant": {"name": "Acme"}, "issuer_id": "ISS1", "transaction": {"amount": 42.0, "currency": "USD"}}},
            )
        ),
        normalize_event(_canonical("cardinal_stepup_oob_log", "a2", "2026-08-21T09:00:02Z", "otp_input", "TXN-1", {"flow": {"transaction_id": "TXN-1"}})),
        normalize_event(
            _canonical("cardinal_stepup_oob_log", "a3", "2026-08-21T09:00:04Z", "vplus_mq_timeout", "TXN-1", {"flow": {"transaction_id": "TXN-1"}}, level="ERROR")
        ),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    lifecycle = reconstruct_lifecycle(flow, events)
    failure_result = analyze_failures(events, flows=result.flows)
    case = build_investigation_case(flow, events, lifecycle, findings=failure_result.findings, conflicts=result.conflicts)
    return case, events, flow, lifecycle


def _pending_oob_case():
    events = [
        normalize_event(
            _canonical("cardinal_stepup_oob_log", "p1", "2026-08-21T09:00:00Z", "vplus_input", "TXN-P", {"flow": {"transaction_id": "TXN-P", "authentication": {"type": "OUTOFBAND"}}})
        ),
        normalize_event(
            _canonical("cardinal_stepup_oob_log", "p2", "2026-08-21T09:00:05Z", "oob_authenticate_api", "TXN-P", {"flow": {"transaction_id": "TXN-P", "authentication": {"type": "OUTOFBAND"}}})
        ),
        normalize_event(
            _canonical("cardinal_stepup_oob_log", "p3", "2026-08-21T09:00:10Z", "oob_status_poll", "TXN-P", {"flow": {"transaction_id": "TXN-P"}, "oob": {"status_history": ["PENDING"]}})
        ),
    ]
    result = correlate_events(events)
    flow = result.flows[0]
    lifecycle = reconstruct_lifecycle(flow, events)
    case = build_investigation_case(flow, events, lifecycle)
    return case, events, flow, lifecycle


def _conflicting_case():
    events = [
        normalize_event(_canonical("cardinal_stepup_oob_log", "a", "2026-08-21T09:00:00Z", "vplus_input", "TXN1", {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "b", "2026-08-21T09:00:01Z", "vplus_input", "TXN1", {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "c", "2026-08-21T09:05:00Z", "vplus_input", "TXN2", {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}})),
        normalize_event(_canonical("cardinal_stepup_oob_log", "d", "2026-08-21T09:05:01Z", "vplus_input", "TXN2", {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}})),
    ]
    result = correlate_events(events)
    target = next(f for f in result.flows if f.transaction_id == "TXN1")
    lifecycle = reconstruct_lifecycle(target, events)
    case = build_investigation_case(target, events, lifecycle, conflicts=result.conflicts)
    return case, events, target, lifecycle


def _mock_client(mocker, response=None, error=None, available=True):
    mock_client = mocker.MagicMock(spec=OllamaClient)
    mock_client.health_check.return_value = (available, "OK" if available else "down")
    mock_client.generate_json.return_value = (response, error)
    return mock_client


# ---------------------------------------------------------------------------
# Deterministic assembly -- never touches the LLM
# ---------------------------------------------------------------------------

def test_deterministic_case_has_no_narrative_before_llm_runs():
    case, *_ = _failed_otp_case()
    assert case.case_summary.narrative == []
    assert case.ai_available is False


def test_case_summary_facts_are_populated_from_deterministic_engine():
    case, *_ = _failed_otp_case()
    assert case.case_summary.transaction_id == "TXN-1"
    assert case.case_summary.merchant_name == "Acme"
    assert case.case_summary.amount == 42.0
    assert case.case_summary.currency == "USD"
    assert case.case_summary.issuer_id == "ISS1"
    assert case.case_summary.authentication_method == "OTP"
    assert case.case_summary.final_status == "FAILED"
    assert case.case_summary.last_successful_stage == "OTP_GENERATED"
    assert case.case_summary.missing_next_stage is not None
    assert "not found in captured logs" in case.case_summary.missing_next_stage


def test_findings_and_routing_populated_deterministically():
    case, *_ = _failed_otp_case()
    assert len(case.findings) == 1
    assert case.findings[0].finding_type == "V_PLUS_MQ_TIMEOUT"
    assert len(case.routing) == 1
    assert case.routing[0].suggested_team == "Middleware / V+"


def test_timeline_never_exposes_raw_reference_to_llm_payload():
    """The InvestigationCase.timeline itself may retain raw_reference for
    internal traceability, but the safe LLM projection must never include
    it -- this is what enforces "the AI must not analyze raw logs
    directly", structurally rather than by prompt instruction alone."""
    from backend.analysis.investigation import build_llm_safe_timeline

    case, *_ = _failed_otp_case()
    assert any(entry.raw_reference for entry in case.timeline)  # the case itself DOES retain it

    safe_timeline = build_llm_safe_timeline(case.timeline)
    dumped = json.dumps(safe_timeline)
    assert "raw_reference" not in dumped
    assert "raw-a1" not in dumped and "raw-a2" not in dumped and "raw-a3" not in dumped


def test_similar_incidents_lookup_is_deterministic_and_excludes_self():
    from backend.analysis.failure_schema import Finding, FailureSignature, Severity, Confidence, RoutingArea

    def _f(sig):
        return Finding(
            finding_type=sig, severity=Severity.HIGH, statement="x", confidence=Confidence.HIGH,
            suggested_route=RoutingArea.MIDDLEWARE_VPLUS,
        )

    target_findings = [_f(FailureSignature.V_PLUS_MQ_TIMEOUT)]
    pool = {
        "flow:target": target_findings,
        "flow:other-same": [_f(FailureSignature.V_PLUS_MQ_TIMEOUT)],
        "flow:other-different": [_f(FailureSignature.PARSER_FAILURE)],
    }
    similar = find_similar_incidents("flow:target", target_findings, pool)
    assert similar == ["flow:other-same"]


def test_no_findings_means_no_similar_incidents():
    similar = find_similar_incidents("flow:x", [], {"flow:y": []})
    assert similar == []


# ---------------------------------------------------------------------------
# LLM narrative -- happy path (mocked)
# ---------------------------------------------------------------------------

def test_valid_llm_statements_are_accepted(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={
            "statements": [
                {
                    "role": "what_happened",
                    "statement_type": "FACT",
                    "text": "The flow reached OTP_GENERATED before a V+ MQ timeout was recorded.",
                    "evidence_event_ids": ["a2", "a3"],
                    "source_files": ["cardinal_stepup_oob_log.log"],
                }
            ]
        },
    )
    assistant = InvestigationAssistant(mock_client)
    result_case, status = assistant.explain_flow(case)

    assert result_case.ai_available is True
    texts = [s.text for s in result_case.case_summary.narrative]
    assert any("MQ timeout" in t for t in texts)


def test_ollama_unavailable_falls_back_to_deterministic_only(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(mocker, available=False)
    assistant = InvestigationAssistant(mock_client)
    result_case, status = assistant.explain_flow(case)

    assert result_case.ai_available is False
    assert "unavailable" in status.lower()
    # deterministic missing-evidence statement still present
    assert any("not found in captured logs" in s.text for s in result_case.case_summary.narrative)


def test_malformed_llm_response_falls_back_gracefully(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(mocker, response=None, error="Invalid JSON")
    assistant = InvestigationAssistant(mock_client)
    result_case, status = assistant.explain_flow(case)

    assert result_case.ai_available is False
    assert "failed" in status.lower()


# ---------------------------------------------------------------------------
# Adversarial LLM responses -- the AI must not make unsupported conclusions
# ---------------------------------------------------------------------------

def test_fraud_inference_is_discarded(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "next_investigation_area", "statement_type": "INTERPRETATION", "text": "This looks like fraud.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert not any("fraud" in s.text.lower() for s in result_case.case_summary.narrative)


def test_customer_intent_inference_is_discarded(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "what_happened", "statement_type": "INTERPRETATION", "text": "The customer likely intended to abandon the purchase.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert not any("intend" in s.text.lower() for s in result_case.case_summary.narrative)


def test_delivery_claim_from_queue_confirmation_is_discarded(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "missing_evidence", "statement_type": "INTERPRETATION", "text": "The SMS was delivered to the customer.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert not any("delivered" in s.text.lower() for s in result_case.case_summary.narrative)


def test_fabricated_event_id_is_discarded(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "supporting_evidence", "statement_type": "FACT", "text": "Confirmed via event xyz-999.", "evidence_event_ids": ["xyz-999"], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert not any("xyz-999" in eid for s in result_case.case_summary.narrative for eid in s.evidence_event_ids)
    assert not any("xyz-999" in s.text for s in result_case.case_summary.narrative)


def test_fabricated_source_file_is_discarded(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "supporting_evidence", "statement_type": "FACT", "text": "See totally-made-up.log.", "evidence_event_ids": [], "source_files": ["totally-made-up.log"]}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert not any("totally-made-up.log" in s.source_files for s in result_case.case_summary.narrative)


def test_bare_long_digit_sequence_is_discarded_as_possible_secret(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "supporting_evidence", "statement_type": "FACT", "text": "The card ending was verified against 4111111111111111.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert not any("4111111111111111" in s.text for s in result_case.case_summary.narrative)


def test_invalid_role_is_discarded(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "not_a_real_role", "statement_type": "FACT", "text": "Some text.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert result_case.case_summary.narrative == [] or all(s.role.value != "not_a_real_role" for s in result_case.case_summary.narrative)


def test_invalid_statement_type_is_discarded(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "what_happened", "statement_type": "DEFINITELY_TRUE", "text": "Some text.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert not any(s.text == "Some text." for s in result_case.case_summary.narrative)


# ---------------------------------------------------------------------------
# OOB PENDING must never become a technical failure
# ---------------------------------------------------------------------------

def test_pending_oob_called_failed_is_discarded(mocker):
    case, *_ = _pending_oob_case()
    assert case.case_summary.final_status == "PENDING_AT_LOG_END"
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "where_flow_stopped", "statement_type": "INTERPRETATION", "text": "This OOB challenge has failed to complete.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert not any("fail" in s.text.lower() for s in result_case.case_summary.narrative)


def test_pending_oob_legitimate_statement_survives(mocker):
    """The forbidden-content check is scoped to false "failure" claims,
    not to the word "pending" itself -- a truthful pending statement must
    still pass through."""
    case, *_ = _pending_oob_case()
    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "where_flow_stopped", "statement_type": "FACT", "text": "The OOB challenge is still pending as of the last captured event.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    assert any("still pending" in s.text for s in result_case.case_summary.narrative)


# ---------------------------------------------------------------------------
# CORRELATION CONFLICT must always be explicit, regardless of the model
# ---------------------------------------------------------------------------

def test_correlation_conflict_is_always_present_even_if_model_omits_it(mocker):
    case, *_ = _conflicting_case()
    assert case.correlation.is_conflict is True
    mock_client = _mock_client(mocker, response={"statements": []})
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)

    assert any(s.text.startswith("CORRELATION CONFLICT") for s in result_case.case_summary.narrative)


def test_correlation_conflict_statement_names_the_conflicting_identifiers(mocker):
    case, *_ = _conflicting_case()
    mock_client = _mock_client(mocker, response={"statements": []})
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)

    conflict_statement = next(s for s in result_case.case_summary.narrative if s.text.startswith("CORRELATION CONFLICT"))
    assert "TXN1" in conflict_statement.text
    assert "TXN2" in conflict_statement.text


def test_llm_conflict_statement_with_correct_phrase_is_kept(mocker):
    case, *_ = _conflicting_case()
    mock_client = _mock_client(
        mocker,
        response={
            "statements": [
                {
                    "role": "where_flow_stopped",
                    "statement_type": "FACT",
                    "text": "CORRELATION CONFLICT: two transactions shared a tracker but disagree on transaction_id.",
                    "evidence_event_ids": ["a", "b", "c", "d"],
                    "source_files": ["cardinal_stepup_oob_log.log"],
                }
            ]
        },
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    conflict_statements = [s for s in result_case.case_summary.narrative if s.text.startswith("CORRELATION CONFLICT")]
    assert len(conflict_statements) == 1  # the model's own valid statement satisfied the requirement, no duplicate injected


# ---------------------------------------------------------------------------
# Deterministic engine remains authoritative -- the AI cannot change facts
# ---------------------------------------------------------------------------

def test_ai_narrative_never_changes_deterministic_case_summary_fields(mocker):
    case, *_ = _failed_otp_case()
    original_final_status = case.case_summary.final_status
    original_last_stage = case.case_summary.last_successful_stage

    mock_client = _mock_client(
        mocker,
        response={"statements": [{"role": "what_happened", "statement_type": "FACT", "text": "The transaction succeeded.", "evidence_event_ids": [], "source_files": []}]},
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)

    # the LLM's text may say whatever it wants in isolation, but the
    # structured facts it was never given write-access to must be unchanged
    assert result_case.case_summary.final_status == original_final_status == "FAILED"
    assert result_case.case_summary.last_successful_stage == original_last_stage


def test_deterministic_findings_routing_correlation_unaffected_by_llm(mocker):
    case, *_ = _failed_otp_case()
    findings_before = [f.model_dump() for f in case.findings]
    routing_before = [r.model_dump() for r in case.routing]
    correlation_before = case.correlation.model_dump()

    mock_client = _mock_client(mocker, response={"statements": []})
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)

    assert [f.model_dump() for f in result_case.findings] == findings_before
    assert [r.model_dump() for r in result_case.routing] == routing_before
    assert result_case.correlation.model_dump() == correlation_before


# ---------------------------------------------------------------------------
# FACT / INTERPRETATION / MISSING EVIDENCE distinction
# ---------------------------------------------------------------------------

def test_statement_types_are_preserved_and_distinct(mocker):
    case, *_ = _failed_otp_case()
    mock_client = _mock_client(
        mocker,
        response={
            "statements": [
                {"role": "what_happened", "statement_type": "FACT", "text": "A timeout event was recorded.", "evidence_event_ids": ["a3"], "source_files": []},
                {"role": "next_investigation_area", "statement_type": "INTERPRETATION", "text": "This may indicate intermittent V+ connectivity issues.", "evidence_event_ids": [], "source_files": []},
            ]
        },
    )
    result_case, _ = InvestigationAssistant(mock_client).explain_flow(case)
    types_seen = {s.statement_type.value for s in result_case.case_summary.narrative}
    assert "FACT" in types_seen
    assert "INTERPRETATION" in types_seen
    assert "MISSING EVIDENCE" in types_seen  # from the mandatory deterministic injection
