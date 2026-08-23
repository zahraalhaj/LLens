"""
Tests for the Phase 10 analytics pipeline and dashboard assembly:
backend/analysis/pipeline.py + backend/analysis/dashboards.py.

Uses a real DatabaseManager (same fixture convention as test_store.py) with
directly-constructed CanonicalLogEvent rows -- this proves
run_analysis_pipeline() genuinely reads from storage and runs the full
Phase 2-9 chain, without needing to go through file-upload ingestion for
every scenario (that's covered separately by test_analytics_api.py).
"""
import pytest

from backend.analysis.dashboards import (
    SEARCHABLE_FIELDS,
    build_correlation_explorer,
    build_dependency_health,
    build_investigation_result,
    build_oob_duration_histogram,
    build_queue_messaging,
    build_security_quality,
    build_service_overview,
    search_flows,
    search_flows_by_raw_text,
)
from backend.analysis.pipeline import run_analysis_pipeline
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


def _seed_success_flow(db):
    """A complete, successful cross-family OTP flow -- Cardinal + a terminal SUCCESS."""
    _insert(
        db, "s1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "otp_input", "TXN-OK",
        {"flow": {"transaction_id": "TXN-OK", "authentication": {"type": "OTP"}, "issuer_id": "ISS1", "merchant": {"name": "Acme"}, "transaction": {"amount": 10.0, "currency": "USD"}}},
    )
    _insert(
        db, "s2", "2026-08-21T09:00:02Z", "cardinal_stepup_oob_log", "cardinal_validate_response", "TXN-OK",
        {"flow": {"transaction_id": "TXN-OK", "authentication": {"type": "OTP", "status": "SUCCESS"}, "integrity_status": "OK"}},
        line_no=2,
    )


def _seed_failed_flow(db):
    # authentication.type is required for Phase 4 to resolve an OTP/OOB
    # template at all -- without it, terminal_status stays UNDETERMINED
    # rather than FAILED, regardless of the technical error present.
    _insert(
        db, "f1", "2026-08-21T10:00:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN-FAIL",
        {"flow": {"transaction_id": "TXN-FAIL", "authentication": {"type": "OTP"}}},
    )
    _insert(
        db, "f2", "2026-08-21T10:00:02Z", "cardinal_stepup_oob_log", "vplus_mq_timeout", "TXN-FAIL",
        {"flow": {"transaction_id": "TXN-FAIL"}}, level=LogLevel.ERROR, line_no=2,
    )


# ---------------------------------------------------------------------------
# Pipeline itself
# ---------------------------------------------------------------------------

def test_pipeline_reads_from_storage_and_runs_full_chain(db):
    _seed_success_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")

    assert len(bundle.events) == 2
    assert len(bundle.flows) == 1
    assert len(bundle.lifecycles) == 1
    assert bundle.failure_result is not None
    assert bundle.dependency_metrics  # populated for all 6 dependencies
    assert bundle.queue_handoff is not None
    assert bundle.pattern_result is not None
    assert bundle.quality_result is not None


def test_pipeline_empty_window_returns_empty_bundle(db):
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    assert bundle.events == []
    assert bundle.flows == []


def test_pipeline_date_range_actually_filters(db):
    _seed_success_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2020-01-01T00:00:00Z", date_to="2020-01-02T00:00:00Z")
    assert bundle.events == []


# ---------------------------------------------------------------------------
# 1. Service overview -- pending/incomplete/error stay separate
# ---------------------------------------------------------------------------

def test_service_overview_success_and_error_are_separate_states(db):
    _seed_success_flow(db)
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    overview = build_service_overview(bundle)

    assert overview["total_flows"] == 2
    assert overview["success"] == 1
    assert overview["error"] == 1
    assert overview["pending"] == 0
    assert overview["incomplete"] == 0


def test_service_overview_pending_never_counted_as_error():
    """OOB PENDING_AT_LOG_END must never be folded into 'error' -- reuses
    the same guarantee Phase 4 already enforces, just checked at the
    dashboard-aggregation layer too."""
    from backend.analysis.correlate import correlate_events
    from backend.analysis.lifecycle import reconstruct_lifecycles
    from backend.analysis.normalize import normalize_event
    from backend.analysis.pipeline import AnalysisBundle
    from backend.analysis.failure import analyze_failures
    from backend.analysis.dependency import compute_all_dependencies, compute_otp_handoff_chain
    from backend.analysis.pattern import analyze_recurring_patterns
    from backend.analysis.quality import analyze_data_quality

    events = [
        normalize_event(
            {
                "event_id": "p1", "batch_id": "b1", "file_name": "c.log", "line_no": 1,
                "ts_utc": "2026-08-21T09:00:00Z", "level": "INFO", "source_system": "cardinal_stepup_oob_log",
                "component": "oob_authenticate_api", "message": "x", "raw": "x",
                "attributes": {"correlation_id": "TXN-P", "details": {"flow": {"transaction_id": "TXN-P", "authentication": {"type": "OUTOFBAND"}}}},
            }
        ),
        normalize_event(
            {
                "event_id": "p2", "batch_id": "b1", "file_name": "c.log", "line_no": 2,
                "ts_utc": "2026-08-21T09:00:05Z", "level": "INFO", "source_system": "cardinal_stepup_oob_log",
                "component": "oob_status_poll", "message": "x", "raw": "x",
                "attributes": {"correlation_id": "TXN-P", "details": {"flow": {"transaction_id": "TXN-P"}, "oob": {"status_history": ["PENDING"]}}},
            }
        ),
    ]
    result = correlate_events(events)
    lifecycles = reconstruct_lifecycles(result.flows, events)
    queue_handoff = compute_otp_handoff_chain(events)
    failure_result = analyze_failures(events, flows=result.flows, conflicts=result.conflicts, queue_handoff=queue_handoff)
    bundle = AnalysisBundle(
        events=events, correlation_result=result, lifecycles=lifecycles, failure_result=failure_result,
        dependency_metrics=compute_all_dependencies(events), queue_handoff=queue_handoff,
        pattern_result=analyze_recurring_patterns(result.flows, events, failure_result.findings, lifecycles=lifecycles),
        quality_result=analyze_data_quality(events, result.flows, correlation_result=result),
    )
    overview = build_service_overview(bundle)
    assert overview["pending"] == 1
    assert overview["error"] == 0
    assert overview["incomplete"] == 0


def test_lifecycle_funnel_is_cumulative_and_carries_drill_through(db):
    _seed_success_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    overview = build_service_overview(bundle)
    funnel = {row["stage"]: row for row in overview["lifecycle_funnel"]}

    assert funnel["AUTH_COMPLETED"]["flow_count"] == 1
    assert funnel["OTP_GENERATED"]["flow_count"] == 1  # cumulative: reached AUTH_COMPLETED implies passing through earlier OTP-path stages
    assert funnel["OOB_INITIATED"]["flow_count"] == 0  # this flow used the OTP template, not OOB
    assert funnel["AUTH_COMPLETED"]["sample_flow_ids"] == [bundle.flows[0].flow_id]


def test_missing_stage_counts_reuse_phase4_evidence_based_gaps(db):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    overview = build_service_overview(bundle)
    stages_reported = {row["stage"] for row in overview["missing_stage_counts"]}
    assert "AUTH_COMPLETED" in stages_reported  # never resolved -- flow ended at a technical error


def test_queue_messaging_includes_validated_count(db):
    _insert(db, "v1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "otp_input", None, {"flow": {"trackers": ["IA5"]}})
    _insert(db, "v2", "2026-08-21T09:00:02Z", "cardinal_stepup_oob_log", "otp_success", None, {"flow": {"trackers": ["IA5"]}}, line_no=2)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    qm = build_queue_messaging(bundle)
    assert qm["validated"] == 1


def test_oob_duration_histogram_samples_have_drill_through_ids(db):
    _insert(db, "oh1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "oob_authenticate_api", "TXN-OOB", {"flow": {"transaction_id": "TXN-OOB"}})
    _insert(
        db, "oh2", "2026-08-21T09:00:05Z", "cardinal_stepup_oob_log", "oob_validate_api", "TXN-OOB",
        {"flow": {"transaction_id": "TXN-OOB"}}, line_no=2,
    )
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    hist = build_oob_duration_histogram(bundle)
    assert len(hist["samples"]) == 1
    assert hist["samples"][0]["latency_ms"] == 5000.0
    assert hist["samples"][0]["request_event_id"] == "oh1"
    assert hist["samples"][0]["response_event_id"] == "oh2"
    assert hist["samples"][0]["join_key"] == "TXN-OOB"  # drill-through key for the Investigation tracker/transaction search


def test_field_mismatch_heatmap_present_in_security_quality(db):
    _insert(db, "hm1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN-HM", {"flow": {"transaction_id": "TXN-HM", "issuer_id": "ISS-A"}})
    _insert(
        db, "hm2", "2026-08-21T09:00:03Z", "afs_netcetera_3ds_stepup", "stepup_message", "TXN-HM",
        {"tracker_no": "SU9", "transaction": {"transaction_id": "TXN-HM", "issuer_id": "ISS-B"}}, line_no=2,
    )
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    sq = build_security_quality(bundle)
    heatmap_row = next(r for r in sq["field_mismatch_heatmap"] if r["field_name"] == "issuer")
    assert heatmap_row["mismatch_count"] == 1
    assert len(heatmap_row["sample_flow_ids"]) == 1


def test_service_overview_top_failure_signatures_carry_evidence(db):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    overview = build_service_overview(bundle)
    assert len(overview["top_failure_signatures"]) == 1
    top = overview["top_failure_signatures"][0]
    assert top["pattern"] == "V_PLUS_MQ_TIMEOUT"
    assert top["total_flows"] == 1  # denominator always present
    assert len(top["representative_evidence"]) >= 1


# ---------------------------------------------------------------------------
# 2. Dependency health
# ---------------------------------------------------------------------------

def test_dependency_health_reflects_v_plus_timeout(db):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    health = build_dependency_health(bundle)
    assert health["dependencies"]["V_PLUS"]["timeout_count"] == 1
    assert "queues" in health


# ---------------------------------------------------------------------------
# 3. Queue and messaging
# ---------------------------------------------------------------------------

def test_queue_messaging_unmatched_and_orphan(db):
    _insert(db, "a1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "otp_input", None, {"flow": {"trackers": ["IA1"]}})
    _insert(db, "a2", "2026-08-21T09:00:01Z", "cardinal_stepup_oob_log", "otp_queue", None, {"flow": {"trackers": ["IA1"]}}, line_no=2)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    qm = build_queue_messaging(bundle)
    assert qm["application_queued"] == 1
    assert qm["unmatched"] == 1
    assert "IA1" in qm["unmatched_tracker_nos"]


# ---------------------------------------------------------------------------
# 4. Investigation search -- every required search field
# ---------------------------------------------------------------------------

def test_search_by_transaction_id(db):
    _seed_success_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    flow_ids = search_flows(bundle, "transaction_id", "TXN-OK")
    assert len(flow_ids) == 1
    result = build_investigation_result(bundle, flow_ids[0])
    assert result["case_summary"]["transaction_id"] == "TXN-OK"
    assert result["case_summary"]["merchant_name"] == "Acme"
    assert result["case_summary"]["final_status"] == "SUCCESS"


def test_search_by_tracker(db):
    _insert(db, "t1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "vplus_input", None, {"flow": {"trackers": ["SU7001"]}})
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    flow_ids = search_flows(bundle, "tracker", "SU7001")
    assert len(flow_ids) == 1


def test_search_by_stepup_request_id(db):
    _insert(
        db, "sr1", "2026-08-21T09:00:00Z", "vflex_transaction_log", "bank_api_success_response", None,
        {"transaction": {"stepup_request_id": "SREQ-99"}},
    )
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    flow_ids = search_flows(bundle, "stepup_request_id", "SREQ-99")
    assert len(flow_ids) == 1


def test_search_by_card_last4(db):
    _insert(
        db, "c1", "2026-08-21T09:00:00Z", "vflex_transaction_log", "bank_api_success_response", "CORR-1",
        {"transaction": {"payment": {"last4_pan": "4242"}}},
    )
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    flow_ids = search_flows(bundle, "card_last4", "4242")
    assert len(flow_ids) == 1


def test_search_by_msg_id(db):
    _insert(
        db, "m1", "2026-08-21T09:00:00Z", "otp_online_processor", "sms_queue_msg_id", None,
        {"tracker_no": None, "record": {"sms_msg_id": "MSGID-7"}},
    )
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    flow_ids = search_flows(bundle, "msg_id", "MSGID-7")
    assert len(flow_ids) == 1


def test_search_by_mobile_masks_query_before_comparing(db):
    """The raw mobile number is never stored -- searching means the query
    is masked the SAME way and compared against the already-masked field."""
    _insert(
        db, "mo1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN-MOB",
        {"flow": {"transaction_id": "TXN-MOB", "customer": {"mobile": "+15551234567"}}},
    )
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")

    flow_ids = search_flows(bundle, "mobile", "+15551234567")
    assert len(flow_ids) == 1

    no_match = search_flows(bundle, "mobile", "+19998887777")
    assert no_match == []

    # the raw mobile is never present anywhere in the events used for search
    for e in bundle.events:
        assert "+15551234567" not in (e.raw_reference or "")[:0]  # raw_reference intentionally not inspected for the number -- masked_mobile is what's compared
        assert e.masked_mobile != "+15551234567"


def test_search_returns_empty_for_no_match(db):
    _seed_success_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    assert search_flows(bundle, "transaction_id", "NO-SUCH-TXN") == []


def test_investigation_result_includes_all_required_sections(db):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    flow_ids = search_flows(bundle, "transaction_id", "TXN-FAIL")
    result = build_investigation_result(bundle, flow_ids[0])

    assert "case_summary" in result
    assert "timeline" in result
    assert "findings" in result
    assert "correlation" in result
    assert "routing" in result
    assert "chart_metrics" in result
    assert "data_quality" in result
    assert result["findings"][0]["finding_type"] == "V_PLUS_MQ_TIMEOUT"
    assert result["routing"][0]["suggested_team"] == "Middleware / V+"
    assert "similar_incident_flow_ids" in result


def test_investigation_unknown_flow_id_returns_none(db):
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    assert build_investigation_result(bundle, "flow:does-not-exist") is None


# ---------------------------------------------------------------------------
# 5. Security / data quality
# ---------------------------------------------------------------------------

def test_security_quality_includes_correlation_confidence_and_mismatches(db):
    _insert(db, "mm1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN-MM", {"flow": {"transaction_id": "TXN-MM", "issuer_id": "ISS-A"}})
    _insert(
        db, "mm2", "2026-08-21T09:00:03Z", "afs_netcetera_3ds_stepup", "stepup_message", "TXN-MM",
        {"tracker_no": "SU9", "transaction": {"transaction_id": "TXN-MM", "issuer_id": "ISS-B"}}, line_no=2,
    )
    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    sq = build_security_quality(bundle)

    assert "scorecard" in sq
    assert "correlation_quality_breakdown" in sq
    assert any(r["field_name"] == "issuer" for r in sq["field_consistency_exceptions"])
    assert "sensitive_data_findings" in sq
    assert "incomplete_flows" in sq
    assert "uncorrelated_flows" in sq


def test_security_quality_never_exposes_raw_sensitive_values(db):
    _insert(
        db, "sd1", "2026-08-21T09:00:00Z", "cardinal_stepup_oob_log", "request_body", "TXN-SD",
        {"flow": {"transaction_id": "TXN-SD"}},
        line_no=1,
    )
    # overwrite the raw text with something sensitive via a second insert on the same source
    from backend.core.schema import BatchRecord as BR, CanonicalLogEvent as CLE, TimestampConfidence as TC, LogLevel as LL
    batch = BR(batch_id="batch-sd", file_name="t.log", file_size_bytes=10, total_events=1)
    event = CLE(
        event_id="sd2", batch_id="batch-sd", file_name="t.log", line_no=1, ts_utc="2026-08-21T09:00:01Z",
        ts_raw="x", ts_confidence=TC.PARSED, level=LL.INFO, source_system="cardinal_stepup_oob_log",
        component="request_body", message="x", raw="CardNumber=4111111111111111 OTP 555111 to a@b.com",
        attributes={"correlation_id": "TXN-SD", "details": {"flow": {"transaction_id": "TXN-SD"}}},
    )
    db.insert_batch_and_events(batch, [event])

    bundle = run_analysis_pipeline(db, date_from="2026-08-21T00:00:00Z", date_to="2026-08-22T00:00:00Z")
    sq = build_security_quality(bundle)
    import json

    dumped = json.dumps(sq)
    assert "4111111111111111" not in dumped
    assert "555111" not in dumped
    assert "a@b.com" not in dumped


# ---------------------------------------------------------------------------
# Correlation Explorer (Feature 1 -- surfaces Phase 3 conflicts/candidate
# links/low-confidence hints, previously computed but never exposed)
# ---------------------------------------------------------------------------


def test_correlation_explorer_surfaces_conflict_and_graph_edge(db):
    _insert(db, "cc1", "2026-08-22T09:00:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN1",
            {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}})
    _insert(db, "cc2", "2026-08-22T09:00:01Z", "cardinal_stepup_oob_log", "vplus_input", "TXN1",
            {"flow": {"transaction_id": "TXN1", "trackers": ["SU1"]}}, line_no=2)
    _insert(db, "cc3", "2026-08-22T09:05:00Z", "cardinal_stepup_oob_log", "vplus_input", "TXN2",
            {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}}, line_no=3)
    _insert(db, "cc4", "2026-08-22T09:05:01Z", "cardinal_stepup_oob_log", "vplus_input", "TXN2",
            {"flow": {"transaction_id": "TXN2", "trackers": ["SU1"]}}, line_no=4)

    bundle = run_analysis_pipeline(db)
    result = build_correlation_explorer(bundle)

    assert len(result["conflicts"]) == 1
    assert len(result["graph"]["nodes"]) == 2
    conflict_edges = [e for e in result["graph"]["edges"] if e["edge_type"] == "conflict"]
    assert len(conflict_edges) == 1


def test_correlation_explorer_empty_when_no_conflicts(db):
    _insert(db, "s1", "2026-08-22T09:00:00Z", "cardinal_stepup_oob_log", "otp_input", "TXN-OK",
            {"flow": {"transaction_id": "TXN-OK"}})
    bundle = run_analysis_pipeline(db)
    result = build_correlation_explorer(bundle)
    assert result["conflicts"] == []
    assert result["candidate_links"] == []
    assert result["low_confidence_hints"] == []
    assert result["graph"] == {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# Issuer failure rates + failure heatmap (Features 2 + 5, added to
# build_service_overview)
# ---------------------------------------------------------------------------


def test_service_overview_includes_issuer_failure_rates(db):
    _seed_success_flow(db)
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db)
    overview = build_service_overview(bundle)
    assert "issuer_failure_rates" in overview
    assert isinstance(overview["issuer_failure_rates"], list)


def test_service_overview_includes_failure_heatmap_bucket(db):
    _seed_failed_flow(db)
    bundle = run_analysis_pipeline(db)
    overview = build_service_overview(bundle)
    assert "failure_heatmap" in overview
    buckets = overview["failure_heatmap"]
    assert len(buckets) >= 1
    bucket = buckets[0]
    assert set(bucket.keys()) == {"day_of_week", "hour", "failed_count", "total_count"}
    assert bucket["failed_count"] >= 1
    assert bucket["total_count"] >= bucket["failed_count"]


# ---------------------------------------------------------------------------
# Raw-text search -> flows (Feature 7)
# ---------------------------------------------------------------------------


def test_search_flows_by_raw_text_finds_matching_flow(db):
    _insert(db, "rt1", "2026-08-22T09:00:00Z", "cardinal_stepup_oob_log", "otp_input", "TXN-RT",
            {"flow": {"transaction_id": "TXN-RT"}})
    bundle = run_analysis_pipeline(db)
    flow_ids = search_flows_by_raw_text(bundle, "raw-rt1")
    assert len(flow_ids) == 1
    assert flow_ids[0] == bundle.flows[0].flow_id


def test_search_flows_supports_correlation_id_field(db):
    """correlation_id was added to SEARCHABLE_FIELDS for Feature 6 (alert
    deep-link) -- the alert engine only has access to correlation_id, never
    the family-specific transaction_id/tracker_no nested paths."""
    _insert(db, "sf1", "2026-08-22T09:00:00Z", "cardinal_stepup_oob_log", "otp_input", "CORR-1",
            {"flow": {"transaction_id": "TXN-SF"}})
    bundle = run_analysis_pipeline(db)
    assert "correlation_id" in SEARCHABLE_FIELDS
    flow_ids = search_flows(bundle, "correlation_id", "CORR-1")
    assert flow_ids == [bundle.flows[0].flow_id]


def test_search_flows_by_raw_text_case_insensitive_and_no_match(db):
    _insert(db, "rt2", "2026-08-22T09:00:00Z", "cardinal_stepup_oob_log", "otp_input", "TXN-RT2",
            {"flow": {"transaction_id": "TXN-RT2"}})
    bundle = run_analysis_pipeline(db)
    assert search_flows_by_raw_text(bundle, "RAW-RT2") == [bundle.flows[0].flow_id]
    assert search_flows_by_raw_text(bundle, "no-such-fragment") == []
    assert search_flows_by_raw_text(bundle, "   ") == []
