"""
Tests for the Phase 11 AI Analyst deterministic tool registry:
backend/analysis/query_tools.py.

Same DatabaseManager + run_analysis_pipeline fixture convention as
test_pipeline.py -- proves every tool function is a pure reshaping of the
real Phase 2-10 pipeline output, with no independent computation.
"""
import pytest

from backend.analysis.pipeline import run_analysis_pipeline
from backend.analysis.query_tools import (
    TOOL_PARAM_ALLOWLIST,
    TOOL_REGISTRY,
    TOOL_SPECS,
    correlation_quality_tool,
    dependency_health_tool,
    issuer_failure_rates_tool,
    list_flows_by_status_tool,
    lookup_transaction,
    queue_handoff_tool,
    recurring_incidents_tool,
    result_has_no_evidence,
    top_failures_tool,
)
from backend.core.schema import BatchRecord, CanonicalLogEvent, LogLevel, TimestampConfidence
from backend.core.store import DatabaseManager


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(db_path=str(tmp_path / "test_logs.db"))


def _insert(db, event_id, ts, source_system, component, correlation_id, details, level=LogLevel.INFO, batch_id=None, file_name="test.log", line_no=1):
    batch_id = batch_id or f"batch-{event_id}"
    batch = BatchRecord(batch_id=batch_id, file_name=file_name, file_size_bytes=100, total_events=1)
    event = CanonicalLogEvent(
        event_id=event_id,
        batch_id=batch_id,
        file_name=file_name,
        line_no=line_no,
        ts_utc=ts,
        ts_raw=ts,
        ts_confidence=TimestampConfidence.PARSED,
        level=level,
        source_system=source_system,
        component=component,
        message="demo",
        raw=f"raw-{event_id}",
        attributes={"correlation_id": correlation_id, "details": details},
    )
    db.insert_batch_and_events(batch, [event])


def _seed_success_flow(db, txn="TXN-OK", issuer="ISS1"):
    _insert(
        db, f"{txn}-1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "otp_input", txn,
        {"flow": {"transaction_id": txn, "authentication": {"type": "OTP"}, "issuer_id": issuer, "merchant": {"name": "Acme"}, "transaction": {"amount": 10.0, "currency": "USD"}}},
    )
    _insert(
        db, f"{txn}-2", "2026-08-21T09:00:02Z", "cardinal_stepup_oob_log", "cardinal_validate_response", txn,
        {"flow": {"transaction_id": txn, "authentication": {"type": "OTP", "status": "SUCCESS"}, "integrity_status": "OK"}},
        line_no=2,
    )


def _seed_failed_flow(db, txn="TXN-FAIL", issuer="ISS2"):
    _insert(
        db, f"{txn}-1", "2026-08-21T10:00:00Z", "cardinal_stepup_oob_log", "vplus_input", txn,
        {"flow": {"transaction_id": txn, "authentication": {"type": "OTP"}, "issuer_id": issuer}},
    )
    _insert(
        db, f"{txn}-2", "2026-08-21T10:00:02Z", "cardinal_stepup_oob_log", "vplus_mq_timeout", txn,
        {"flow": {"transaction_id": txn}}, level=LogLevel.ERROR, line_no=2,
    )


def _seed_pending_oob_flow(db, txn="TXN-OOB"):
    _insert(
        db, f"{txn}-1", "2026-08-21T11:00:00Z", "cardinal_stepup_oob_log", "vplus_input", txn,
        {"flow": {"transaction_id": txn, "authentication": {"type": "OUTOFBAND"}}},
    )
    _insert(
        db, f"{txn}-2", "2026-08-21T11:00:05Z", "cardinal_stepup_oob_log", "oob_status_poll", txn,
        {"flow": {"transaction_id": txn}, "oob": {"status_history": ["PENDING"]}}, line_no=2,
    )


def _bundle(db):
    return run_analysis_pipeline(db)


# ---------------------------------------------------------------------------
# Registry / spec integrity
# ---------------------------------------------------------------------------


def test_registry_and_specs_are_kept_in_sync():
    assert set(TOOL_REGISTRY.keys()) == {spec["name"] for spec in TOOL_SPECS}
    assert set(TOOL_PARAM_ALLOWLIST.keys()) == set(TOOL_REGISTRY.keys())


# ---------------------------------------------------------------------------
# lookup_transaction
# ---------------------------------------------------------------------------


def test_lookup_transaction_finds_seeded_flow(db):
    _seed_success_flow(db)
    bundle = _bundle(db)
    result = lookup_transaction(bundle, "transaction_id", "TXN-OK")
    assert result["match_count"] == 1
    assert result["matches"][0]["case_summary"]["transaction_id"] == "TXN-OK"


def test_lookup_transaction_no_match_returns_zero(db):
    _seed_success_flow(db)
    bundle = _bundle(db)
    result = lookup_transaction(bundle, "transaction_id", "DOES-NOT-EXIST")
    assert result["match_count"] == 0
    assert result_has_no_evidence("lookup_transaction", result)


def test_lookup_transaction_rejects_unsupported_field(db):
    bundle = _bundle(db)
    result = lookup_transaction(bundle, "not_a_real_field", "x")
    assert "error" in result
    assert result_has_no_evidence("lookup_transaction", result)


# ---------------------------------------------------------------------------
# dependency_health
# ---------------------------------------------------------------------------


def test_dependency_health_specific_dependency(db):
    _seed_failed_flow(db)
    bundle = _bundle(db)
    result = dependency_health_tool(bundle, dependency="V_PLUS")
    assert result["dependency"] == "V_PLUS"
    assert result["metrics"] is not None
    assert result["metrics"]["request_count"] >= 1


def test_dependency_health_unknown_dependency_has_no_evidence(db):
    bundle = _bundle(db)
    result = dependency_health_tool(bundle, dependency="NOT_A_REAL_DEPENDENCY")
    assert result_has_no_evidence("dependency_health", result)


def test_dependency_health_all_dependencies_when_omitted(db):
    _seed_failed_flow(db)
    bundle = _bundle(db)
    result = dependency_health_tool(bundle)
    assert result["dependency"] is None
    assert isinstance(result["metrics"], dict)


# ---------------------------------------------------------------------------
# queue_handoff_health
# ---------------------------------------------------------------------------


def test_queue_handoff_tool_reflects_pipeline_report(db):
    _seed_success_flow(db)
    bundle = _bundle(db)
    result = queue_handoff_tool(bundle)
    assert result["report"] is not None
    assert "unmatched_tracker_nos" in result["report"]


# ---------------------------------------------------------------------------
# top_failures / recurring_incidents
# ---------------------------------------------------------------------------


def test_top_failures_lists_ranked_patterns(db):
    _seed_failed_flow(db, txn="TXN-A")
    _seed_failed_flow(db, txn="TXN-B")
    bundle = _bundle(db)
    result = top_failures_tool(bundle, limit=5)
    assert result["total_flows_analyzed"] >= 2
    assert len(result["patterns"]) >= 1
    assert result["patterns"][0]["total_flows"] == result["total_flows_analyzed"]


def test_top_failures_empty_when_no_flows(db):
    bundle = _bundle(db)
    result = top_failures_tool(bundle)
    assert result["patterns"] == []
    assert result_has_no_evidence("top_failures", result)


def test_recurring_incidents_filters_by_threshold(db):
    _seed_failed_flow(db, txn="TXN-A")
    _seed_failed_flow(db, txn="TXN-B")
    bundle = _bundle(db)
    result = recurring_incidents_tool(bundle, min_affected_flows=2)
    assert result["min_affected_flows_threshold"] == 2
    for p in result["recurring_patterns"]:
        assert p["affected_flows"] >= 2


def test_recurring_incidents_empty_below_threshold(db):
    _seed_success_flow(db)
    bundle = _bundle(db)
    result = recurring_incidents_tool(bundle, min_affected_flows=100)
    assert result["recurring_patterns"] == []
    assert result_has_no_evidence("recurring_incidents", result)


# ---------------------------------------------------------------------------
# correlation_quality
# ---------------------------------------------------------------------------


def test_correlation_quality_no_conflicts_has_no_evidence(db):
    _seed_success_flow(db)
    bundle = _bundle(db)
    result = correlation_quality_tool(bundle)
    assert result["conflict_count"] == 0
    assert result_has_no_evidence("correlation_quality", result)


def _seed_conflicting_flows(db):
    # Two flows sharing tracker SU1 with conflicting transaction_ids -- same
    # conflict-construction shape used in test_correlate.py/test_pipeline.py.
    _insert(db, "cc1", "2026-08-22T09:00:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN1",
            {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}})
    _insert(db, "cc2", "2026-08-22T09:00:01Z", "cardinal_stepup_oob_log", "vplus_input", "TXN1",
            {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}}, line_no=2)
    _insert(db, "cc3", "2026-08-22T09:05:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN2",
            {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}}, line_no=3)
    _insert(db, "cc4", "2026-08-22T09:05:01Z", "cardinal_stepup_oob_log", "vplus_input", "TXN2",
            {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}}, line_no=4)


def test_correlation_quality_includes_conflicting_identifier_detail(db):
    _seed_conflicting_flows(db)
    bundle = _bundle(db)
    result = correlation_quality_tool(bundle)
    assert result["conflict_count"] == 1
    conflict = result["conflicts"][0]
    assert conflict["conflicting_identifiers"] == [
        {"key_type": "transaction_id", "flow_a_value": "TXN1", "flow_b_value": "TXN2"}
    ]
    assert conflict["source_event_ids_a"] == ["cc1", "cc2"]
    assert conflict["source_event_ids_b"] == ["cc3", "cc4"]


def test_correlation_quality_filters_by_flow_id(db):
    _seed_conflicting_flows(db)
    bundle = _bundle(db)
    full_result = correlation_quality_tool(bundle)
    flow_a_id, flow_b_id = full_result["conflicts"][0]["affected_flow_ids"]

    result = correlation_quality_tool(bundle, flow_id=flow_a_id)
    assert result["flow_id"] == flow_a_id
    assert result["conflict_count"] == 1
    assert flow_a_id in result["conflicts"][0]["affected_flow_ids"]

    # The conflict touches both ends -- either flow_id surfaces it.
    result_b = correlation_quality_tool(bundle, flow_id=flow_b_id)
    assert result_b["conflict_count"] == 1


def test_correlation_quality_flow_id_with_no_matches_has_no_evidence(db):
    _seed_conflicting_flows(db)
    bundle = _bundle(db)
    result = correlation_quality_tool(bundle, flow_id="flow:transaction_id:UNRELATED:deadbeef")
    assert result["conflict_count"] == 0
    assert result["candidate_link_count"] == 0
    assert result["low_confidence_hint_count"] == 0
    assert result_has_no_evidence("correlation_quality", result)


# ---------------------------------------------------------------------------
# issuer_failure_rates
# ---------------------------------------------------------------------------


def test_issuer_failure_rates_computes_denominators(db):
    _seed_success_flow(db, txn="TXN-OK1", issuer="ISS1")
    _seed_failed_flow(db, txn="TXN-FAIL1", issuer="ISS1")
    bundle = _bundle(db)
    result = issuer_failure_rates_tool(bundle)
    iss1 = next(r for r in result["issuers"] if r["issuer_id"] == "ISS1")
    assert iss1["total_flows"] == 2
    assert iss1["failed_flows"] == 1
    assert iss1["failure_rate"] == 0.5


def test_issuer_failure_rates_empty_when_no_issuer_data(db):
    bundle = _bundle(db)
    result = issuer_failure_rates_tool(bundle)
    assert result["issuers"] == []
    assert result_has_no_evidence("issuer_failure_rates", result)


# ---------------------------------------------------------------------------
# list_flows_by_status
# ---------------------------------------------------------------------------


def test_list_flows_by_status_filters_pending_oob(db):
    _seed_pending_oob_flow(db)
    _seed_success_flow(db)
    bundle = _bundle(db)
    result = list_flows_by_status_tool(bundle, terminal_status="PENDING_AT_LOG_END", auth_template="OOB")
    assert result["match_count"] == 1
    assert result["flows"][0]["transaction_id"] == "TXN-OOB"


def test_list_flows_by_status_rejects_unknown_status(db):
    bundle = _bundle(db)
    result = list_flows_by_status_tool(bundle, terminal_status="NOT_A_REAL_STATUS")
    assert "error" in result
    assert result_has_no_evidence("list_flows_by_status", result)


def test_list_flows_by_status_rejects_unknown_template(db):
    bundle = _bundle(db)
    result = list_flows_by_status_tool(bundle, auth_template="NOT_A_TEMPLATE")
    assert "error" in result


def test_list_flows_by_status_no_filters_matches_everything(db):
    _seed_success_flow(db)
    _seed_failed_flow(db)
    bundle = _bundle(db)
    result = list_flows_by_status_tool(bundle)
    assert result["match_count"] == 2
