"""Hallucination / validation monitoring (backend/llm/validation_metrics.py).

The behaviour under test is that the validation gates still discard exactly
what they discarded before -- and now also count it.
"""
import pytest

from backend.llm.ai_analyst import AIAnalystAssistant
from backend.llm.validation_metrics import (
    HALLUCINATION,
    MALFORMED_OUTPUT,
    POLICY_VIOLATION,
    LLMValidationMetrics,
    RejectionReason,
    Surface,
    ValidationOutcome,
)


@pytest.fixture
def metrics(tmp_path):
    return LLMValidationMetrics(db_path=str(tmp_path / "test.db"))


class _FakeClient:
    model = "qwen3:8b"


def _assistant(metrics):
    """Builds the assistant without going through __init__'s OllamaClient --
    these tests exercise the validation gate, not the transport."""
    assistant = AIAnalystAssistant.__new__(AIAnalystAssistant)
    assistant.client = _FakeClient()
    assistant.validation_metrics = metrics
    return assistant


def test_evidence_gate_still_discards_exactly_what_it_discarded_before(metrics):
    """The counting must be a by-product. What survives the gate is the
    contract other code depends on, and it must not change."""
    tool_result = {"flow_id": "FLOW-1", "events": ["EVT-1"]}
    raw = [
        {"evidence_type": "observed_fact", "text": "Flow FLOW-1 completed.", "flow_ids": ["FLOW-1"]},
        {"evidence_type": "observed_fact", "text": "Event EVT-999 failed.", "evidence_event_ids": ["EVT-999"]},
        {"evidence_type": "observed_fact", "text": "Traced to FLOW-42.", "flow_ids": ["FLOW-42"]},
        "not a dict at all",
        {"evidence_type": "observed_fact", "text": "This was fraud by the customer."},
        {"evidence_type": "not_a_real_type", "text": "Bad type."},
    ]
    evidence, outcome = _assistant(metrics)._validate_evidence(raw, tool_result)

    assert len(evidence) == 1
    assert evidence[0].text == "Flow FLOW-1 completed."

    assert outcome.items_total == 6
    assert outcome.items_accepted == 1
    assert outcome.items_rejected == 5
    assert outcome.reason_counts == {
        RejectionReason.UNKNOWN_EVENT_ID: 1,
        RejectionReason.UNKNOWN_FLOW_ID: 1,
        RejectionReason.MALFORMED_ITEM: 1,
        RejectionReason.FORBIDDEN_CONTENT: 1,
        RejectionReason.INVALID_ROLE_TYPE_OR_EMPTY_TEXT: 1,
    }


def test_fabricated_references_are_classed_as_hallucination(metrics):
    """The class split is the point of the feature: a fabricated event id
    means something different operationally than a JSON syntax error, and
    collapsing both into one 'rejection rate' hides which one is moving."""
    o = ValidationOutcome(surface=Surface.ANALYST_EVIDENCE)
    o.reject(RejectionReason.UNKNOWN_EVENT_ID, "cited EVT-999")
    o.reject(RejectionReason.UNKNOWN_FLOW_ID, "cited FLOW-42")
    o.reject(RejectionReason.FORBIDDEN_CONTENT, "fraud claim")
    o.reject(RejectionReason.MALFORMED_JSON_RESPONSE, "bad json")
    metrics.record(o, "ollama:qwen3:8b")

    by_class = metrics.summary(lookback_hours=1)["by_class"]
    assert by_class[HALLUCINATION] == 2
    assert by_class[POLICY_VIOLATION] == 1
    assert by_class[MALFORMED_OUTPUT] == 1


def test_clean_passes_are_recorded_so_the_rate_has_a_denominator(metrics):
    """"20 rejections" is meaningless without knowing whether that was out
    of 25 responses or 25,000."""
    for _ in range(9):
        ok = ValidationOutcome(surface=Surface.ANALYST_NARRATION)
        ok.accept()
        metrics.record(ok, "ollama:qwen3:8b")
    bad = ValidationOutcome(surface=Surface.ANALYST_NARRATION)
    bad.reject_response(RejectionReason.MALFORMED_JSON_RESPONSE, "unterminated string")
    metrics.record(bad, "ollama:qwen3:8b")

    summary = metrics.summary(lookback_hours=1)
    assert summary["responses_validated"] == 10
    assert summary["responses_with_rejection"] == 1
    assert summary["response_rejection_rate"] == 0.1
    assert summary["responses_fully_rejected"] == 1


def test_rate_is_zero_not_an_error_when_nothing_has_been_recorded(metrics):
    summary = metrics.summary(lookback_hours=1)
    assert summary["responses_validated"] == 0
    assert summary["response_rejection_rate"] == 0.0
    assert summary["item_rejection_rate"] == 0.0
    assert summary["by_reason"] == {}


def test_rejected_sample_is_redacted_before_it_is_stored(metrics):
    """This table must not become a second place a raw PAN/mobile/email
    ends up stored -- the rejected text is model output that can quote log
    content verbatim."""
    o = ValidationOutcome(surface=Surface.ANALYST_EVIDENCE)
    o.reject(
        RejectionReason.FORBIDDEN_CONTENT,
        "Customer 4111111111111111 (jordan.t@ilabank.com, +97333445566) committed fraud",
    )
    metrics.record(o, "ollama:qwen3:8b")

    sample = metrics.list_recent()[0]["sample"]
    assert "4111111111111111" not in sample
    assert "jordan.t@ilabank.com" not in sample
    assert "+97333445566" not in sample
    assert "committed fraud" in sample  # the diagnostic part survives


def test_first_rejected_sample_is_kept_not_the_last(metrics):
    """When a response goes bad it usually goes bad from a point onward;
    the first failure is the more diagnostic one."""
    o = ValidationOutcome(surface=Surface.ANALYST_EVIDENCE)
    o.reject(RejectionReason.UNKNOWN_EVENT_ID, "first failure")
    o.reject(RejectionReason.UNKNOWN_FLOW_ID, "second failure")
    assert o.sample == "first failure"


def test_recording_failure_never_breaks_the_caller(metrics, monkeypatch):
    """A monitoring feature that can take down the thing it monitors is
    worse than no monitoring."""
    def explode():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(metrics, "Session", explode)
    o = ValidationOutcome(surface=Surface.ANALYST_EVIDENCE)
    o.reject(RejectionReason.UNKNOWN_EVENT_ID, "x")
    assert metrics.record(o, "ollama:qwen3:8b") is None  # swallowed, not raised


def test_assistant_without_metrics_still_validates(tmp_path):
    """The gate must not depend on a database being reachable."""
    assistant = _assistant(None)
    evidence, outcome = assistant._validate_evidence(
        [{"evidence_type": "observed_fact", "text": "Bad.", "flow_ids": ["NOPE"]}], {"flow_id": "FLOW-1"}
    )
    assert evidence == []
    assert outcome.reason_counts == {RejectionReason.UNKNOWN_FLOW_ID: 1}
    assistant._record_validation(outcome)  # no-op, must not raise


def test_by_surface_separates_the_gates(metrics):
    for surface in (Surface.ANALYST_EVIDENCE, Surface.CHAT_SQL):
        o = ValidationOutcome(surface=surface)
        o.reject_response(RejectionReason.MALFORMED_JSON_RESPONSE, "x")
        metrics.record(o, "ollama:qwen3:8b")

    by_surface = metrics.summary(lookback_hours=1)["by_surface"]
    assert set(by_surface) == {Surface.ANALYST_EVIDENCE, Surface.CHAT_SQL}
    assert by_surface[Surface.CHAT_SQL]["responses_with_rejection"] == 1


def test_daily_series_is_present_for_trend_detection(metrics):
    """A lifetime average can't distinguish "always been 3%" from "was 0%
    until Tuesday", and only the second one is an incident."""
    o = ValidationOutcome(surface=Surface.ANALYST_EVIDENCE)
    o.reject(RejectionReason.UNKNOWN_EVENT_ID, "x")
    metrics.record(o, "ollama:qwen3:8b")

    daily = metrics.summary(lookback_hours=24)["daily"]
    assert len(daily) == 1
    assert daily[0]["rejections"] == 1
    assert daily[0][HALLUCINATION] == 1


def test_list_recent_returns_only_rejections_by_default(metrics):
    ok = ValidationOutcome(surface=Surface.ANALYST_NARRATION)
    ok.accept()
    metrics.record(ok, "ollama:qwen3:8b")
    bad = ValidationOutcome(surface=Surface.ANALYST_NARRATION)
    bad.reject_response(RejectionReason.MALFORMED_JSON_RESPONSE, "boom")
    metrics.record(bad, "ollama:qwen3:8b")

    assert len(metrics.list_recent()) == 1
    assert len(metrics.list_recent(rejections_only=False)) == 2


def test_prune_drops_old_rows_only(metrics):
    o = ValidationOutcome(surface=Surface.ANALYST_EVIDENCE)
    o.reject(RejectionReason.UNKNOWN_EVENT_ID, "x")
    metrics.record(o, "ollama:qwen3:8b")

    assert metrics.prune(older_than_days=90) == 0
    assert metrics.prune(older_than_days=0) == 1
    assert metrics.summary(lookback_hours=24)["responses_validated"] == 0
