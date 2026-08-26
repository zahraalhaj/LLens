"""
Tests for the Phase 11 user-facing AI Analyst:
backend/llm/ai_analyst.py + backend/analysis/ai_analyst_schema.py.

All LLM calls are mocked (same convention as test_investigation.py) --
these tests verify the deterministic tool-selection/execution and the
validation layer that keeps the AI from inventing tools, ids, or
unsupported conclusions, never a live model. AIAnalystAssistant.ask()
calls generate_json() twice (tool selection, then narration) -- mocks use
side_effect=[...] to supply both in order.
"""
import pytest

from backend.analysis.ai_analyst_schema import Confidence, EvidenceType
from backend.analysis.pipeline import run_analysis_pipeline
from backend.core.schema import BatchRecord, CanonicalLogEvent, LogLevel, TimestampConfidence
from backend.core.store import DatabaseManager
from backend.llm.ai_analyst import AIAnalystAssistant, _NOT_FOUND_PHRASE
from backend.llm.client import OllamaClient


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(db_path=str(tmp_path / "test_logs.db"))


def _insert(db, event_id, ts, source_system, component, correlation_id, details, level=LogLevel.INFO, line_no=1):
    batch_id = f"batch-{event_id}"
    batch = BatchRecord(batch_id=batch_id, file_name="test.log", file_size_bytes=100, total_events=1)
    event = CanonicalLogEvent(
        event_id=event_id, batch_id=batch_id, file_name="test.log", line_no=line_no,
        ts_utc=ts, ts_raw=ts, ts_confidence=TimestampConfidence.PARSED, level=level,
        source_system=source_system, component=component, message="demo", raw=f"raw-{event_id}",
        attributes={"correlation_id": correlation_id, "details": details},
    )
    db.insert_batch_and_events(batch, [event])


def _seed_failed_flow(db, txn="TXN-FAIL"):
    _insert(db, f"{txn}-1", "2026-08-21T10:00:00Z", "cardinal_stepup_oob_log", "vplus_input", txn,
            {"flow": {"transaction_id": txn, "authentication": {"type": "OTP"}, "issuer_id": "ISS1"}})
    _insert(db, f"{txn}-2", "2026-08-21T10:00:02Z", "cardinal_stepup_oob_log", "vplus_mq_timeout", txn,
            {"flow": {"transaction_id": txn}}, level=LogLevel.ERROR, line_no=2)


def _mock_client(mocker, side_effect, available=True):
    mock_client = mocker.MagicMock(spec=OllamaClient)
    mock_client.model = "qwen3:8b"
    mock_client.health_check.return_value = (available, "OK" if available else "down")
    mock_client.generate_json.side_effect = side_effect
    return mock_client


# ---------------------------------------------------------------------------
# Ollama unavailable
# ---------------------------------------------------------------------------


def test_ollama_unavailable_returns_unavailable_answer(mocker, db):
    bundle = run_analysis_pipeline(db)
    mock_client = _mock_client(mocker, side_effect=[], available=False)
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("Is V+ having issues?", bundle)
    assert answer.ai_available is False
    assert answer.unsupported is True
    mock_client.generate_json.assert_not_called()


# ---------------------------------------------------------------------------
# Tool selection safety: unknown/None tool never fabricated
# ---------------------------------------------------------------------------


def test_model_naming_unknown_tool_is_treated_as_unsupported(mocker, db):
    bundle = run_analysis_pipeline(db)
    mock_client = _mock_client(mocker, side_effect=[({"tool": "run_arbitrary_sql", "parameters": {}}, None)])
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("Drop all failed transactions", bundle)
    assert answer.unsupported is True
    assert answer.tool_used is None
    # Only ONE generate_json call -- narration is never reached for an unsupported tool.
    assert mock_client.generate_json.call_count == 1


def test_model_declining_with_null_tool_is_unsupported(mocker, db):
    bundle = run_analysis_pipeline(db)
    mock_client = _mock_client(
        mocker, side_effect=[({"tool": None, "reason": "LLens has no data on customer sentiment."}, None)]
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("Was the customer happy?", bundle)
    assert answer.unsupported is True
    assert answer.limitation_explanation == "LLens has no data on customer sentiment."


def test_unknown_parameter_names_are_dropped_not_passed_through(mocker, db):
    """The model naming a parameter outside the tool's own spec must never
    reach the tool function -- it is silently dropped, not rejected
    outright (extra keys are the model's error, not the user's)."""
    bundle = run_analysis_pipeline(db)
    mock_client = _mock_client(
        mocker,
        side_effect=[
            ({"tool": "queue_handoff_health", "parameters": {"sql_injection": "'; DROP TABLE events; --"}}, None),
            ({"answer": "No queue issues found.", "evidence": [], "confidence": "MEDIUM", "recommended_investigation_area": None}, None),
        ],
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("Are there queue handoff problems?", bundle)
    assert answer.tool_used == "queue_handoff_health"
    assert answer.tool_parameters == {}  # the bogus param never survived sanitization


# ---------------------------------------------------------------------------
# Happy path: deterministic tool result flows through to the answer
# ---------------------------------------------------------------------------


def test_happy_path_dependency_health(mocker, db):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db)
    mock_client = _mock_client(
        mocker,
        side_effect=[
            ({"tool": "dependency_health", "parameters": {"dependency": "V_PLUS"}}, None),
            (
                {
                    "answer": "V+ recorded 1 request in the analyzed window with a timeout.",
                    "evidence": [
                        {"evidence_type": "calculated_metric", "text": "V+ request_count is 1.", "evidence_event_ids": [], "flow_ids": []},
                    ],
                    "confidence": "HIGH",
                    "recommended_investigation_area": "Middleware / V+",
                },
                None,
            ),
        ],
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("Is V+ having issues?", bundle)

    assert answer.tool_used == "dependency_health"
    assert answer.tool_parameters == {"dependency": "V_PLUS"}
    assert answer.metrics["dependency"] == "V_PLUS"
    assert answer.confidence == Confidence.HIGH
    assert answer.recommended_investigation_area == "Middleware / V+"
    assert answer.evidence[0].evidence_type == EvidenceType.CALCULATED_METRIC
    assert answer.ai_available is True


# ---------------------------------------------------------------------------
# Validation layer: fabricated evidence ids are discarded
# ---------------------------------------------------------------------------


def test_evidence_citing_fabricated_event_id_is_discarded(mocker, db):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db)
    mock_client = _mock_client(
        mocker,
        side_effect=[
            ({"tool": "queue_handoff_health", "parameters": {}}, None),
            (
                {
                    "answer": "No handoff issues.",
                    "evidence": [
                        {
                            "evidence_type": "observed_fact",
                            "text": "Event xyz-not-real proves everything is fine.",
                            "evidence_event_ids": ["xyz-not-real"],
                            "flow_ids": [],
                        }
                    ],
                    "confidence": "HIGH",
                    "recommended_investigation_area": None,
                },
                None,
            ),
        ],
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("Are there queue handoff problems?", bundle)
    assert all("xyz-not-real" not in e.evidence_event_ids for e in answer.evidence)


@pytest.mark.parametrize(
    "forbidden_text",
    [
        "This transaction is fraudulent.",
        "The customer likely tried to bypass verification.",
        "The OTP was delivered to the customer.",
    ],
)
def test_forbidden_content_is_discarded(mocker, db, forbidden_text):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db)
    mock_client = _mock_client(
        mocker,
        side_effect=[
            ({"tool": "queue_handoff_health", "parameters": {}}, None),
            (
                {
                    "answer": "See evidence.",
                    "evidence": [
                        {"evidence_type": "inferred_interpretation", "text": forbidden_text, "evidence_event_ids": [], "flow_ids": []}
                    ],
                    "confidence": "LOW",
                    "recommended_investigation_area": None,
                },
                None,
            ),
        ],
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("Are there queue handoff problems?", bundle)
    assert all(forbidden_text != e.text for e in answer.evidence)


# ---------------------------------------------------------------------------
# Missing-evidence guarantee: enforced regardless of what the model wrote
# ---------------------------------------------------------------------------


def test_empty_result_forces_low_confidence_and_exact_phrase(mocker, db):
    bundle = run_analysis_pipeline(db)  # nothing seeded -- lookup will find nothing
    mock_client = _mock_client(
        mocker,
        side_effect=[
            ({"tool": "lookup_transaction", "parameters": {"field": "transaction_id", "value": "TXN-GHOST"}}, None),
            (
                {
                    "answer": "Transaction TXN-GHOST was not found.",
                    "evidence": [],
                    "confidence": "HIGH",  # model overconfident on empty data -- must be overridden
                    "recommended_investigation_area": None,
                },
                None,
            ),
        ],
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("What happened to transaction TXN-GHOST?", bundle)
    assert answer.confidence == Confidence.LOW
    assert any(e.evidence_type == EvidenceType.MISSING_EVIDENCE and _NOT_FOUND_PHRASE in e.text for e in answer.evidence)


def test_narration_failure_still_returns_metrics_with_fallback_text(mocker, db):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db)
    mock_client = _mock_client(
        mocker,
        side_effect=[
            ({"tool": "top_failures", "parameters": {}}, None),
            (None, "Ollama connection error: timeout"),
        ],
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("What are the top failures today?", bundle)
    assert answer.ai_available is False
    assert answer.tool_used == "top_failures"
    assert answer.metrics["tool"] == "top_failures"
    assert len(answer.metrics["patterns"]) >= 1


# ---------------------------------------------------------------------------
# Correlation conflict mandatory flag
# ---------------------------------------------------------------------------


def test_correlation_conflict_present_forces_mandatory_statement(mocker, db):
    # Two flows sharing tracker SU1 with conflicting transaction_ids -- the
    # same conflict-construction shape used in test_correlate.py.
    _insert(db, "c1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN1",
            {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}})
    _insert(db, "c2", "2026-08-21T09:00:01Z", "cardinal_stepup_oob_log", "vplus_input", "TXN1",
            {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}}, line_no=2)
    _insert(db, "c3", "2026-08-21T09:05:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN2",
            {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}}, line_no=3)
    _insert(db, "c4", "2026-08-21T09:05:01Z", "cardinal_stepup_oob_log", "vplus_input", "TXN2",
            {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}}, line_no=4)
    bundle = run_analysis_pipeline(db)
    assert bundle.correlation_result.conflicts  # sanity: the seeded data really produced a conflict

    mock_client = _mock_client(
        mocker,
        side_effect=[
            ({"tool": "correlation_quality", "parameters": {}}, None),
            ({"answer": "No conflicts observed.", "evidence": [], "confidence": "HIGH", "recommended_investigation_area": None}, None),
        ],
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask("Are there correlation problems in the logs?", bundle)
    assert any("CORRELATION CONFLICT" in e.text for e in answer.evidence)


def test_correlation_quality_flow_id_scoped_question_cites_conflicting_values(mocker, db):
    # Same conflict shape as above, but this time the model is given a
    # flow_id (as it would be after tool selection extracts one from a "why
    # was X flagged as conflicting" question) and the narration cites the
    # conflicting_identifiers detail that's only present once filtered.
    _insert(db, "c1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN1",
            {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}})
    _insert(db, "c2", "2026-08-21T09:00:01Z", "cardinal_stepup_oob_log", "vplus_input", "TXN1",
            {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}}, line_no=2)
    _insert(db, "c3", "2026-08-21T09:05:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN2",
            {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}}, line_no=3)
    _insert(db, "c4", "2026-08-21T09:05:01Z", "cardinal_stepup_oob_log", "vplus_input", "TXN2",
            {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}}, line_no=4)
    bundle = run_analysis_pipeline(db)
    flow_a_id = bundle.correlation_result.conflicts[0].affected_flow_ids[0]

    mock_client = _mock_client(
        mocker,
        side_effect=[
            ({"tool": "correlation_quality", "parameters": {"flow_id": flow_a_id}}, None),
            (
                {
                    "answer": "This flow conflicts with another flow because they share tracker SU1 but disagree on transaction_id.",
                    "evidence": [
                        {
                            "evidence_type": "observed_fact",
                            "text": "transaction_id disagreed: TXN1 vs TXN2.",
                            "evidence_event_ids": [],
                            "flow_ids": [flow_a_id],
                        }
                    ],
                    "confidence": "HIGH",
                    "recommended_investigation_area": None,
                },
                None,
            ),
        ],
    )
    assistant = AIAnalystAssistant(mock_client)
    answer = assistant.ask(f"Why was flow {flow_a_id} flagged as conflicting?", bundle)

    assert answer.tool_used == "correlation_quality"
    assert answer.tool_parameters == {"flow_id": flow_a_id}
    assert any("TXN1 vs TXN2" in e.text for e in answer.evidence)
    # The mandatory conflict statement scopes its wording to the flow when
    # flow_id was used, instead of the whole-window phrasing.
    assert any(f"involving flow '{flow_a_id}'" in e.text for e in answer.evidence)
