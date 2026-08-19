from datetime import datetime, timezone

from backend.analysis.vplus_monitoring import (
    compute_investigation_summary,
    compute_response_time_stats,
    compute_sms_analysis,
    compute_vplus_availability,
    group_events_by_transaction,
)


def evt(ts, component, corr_id, level="INFO", message="msg", tx=None, tracker="SU1"):
    details = {"tracker_no": tracker}
    if tx:
        details["transaction"] = tx
    return {
        "ts_utc": ts, "component": component, "level": level, "message": message,
        "attributes": {"correlation_id": corr_id, "details": details},
    }


# -- grouping -----------------------------------------------------------------

def test_group_events_by_transaction_groups_by_correlation_id():
    events = [evt("2026-08-18T10:00:00Z", "vplus_input", "TXN1"), evt("2026-08-18T10:00:01Z", "sms_input", "TXN1"),
              evt("2026-08-18T10:00:02Z", "vplus_input", "TXN2")]
    groups = group_events_by_transaction(events)
    assert len(groups) == 2
    assert len(groups["TXN1"]) == 2


def test_group_events_ignores_events_without_correlation_id():
    events = [{"ts_utc": "x", "component": "other", "attributes": {}}]
    groups = group_events_by_transaction(events)
    assert groups == {}


# -- availability ---------------------------------------------------------------

def test_availability_no_events_returns_no_data():
    result = compute_vplus_availability([])
    assert result["status"] == "no_data"
    assert result["currently_down"] is None


def test_availability_detects_gap_as_downtime_window():
    events = [
        evt("2026-08-18T10:00:00Z", "vplus_input", "TXN1"),
        evt("2026-08-18T10:00:01Z", "vplus_response", "TXN1"),
        evt("2026-08-18T10:30:00Z", "vplus_input", "TXN2"),  # 30 min gap
    ]
    result = compute_vplus_availability(events, gap_threshold_minutes=10, reference_now=datetime(2026, 8, 18, 10, 30, tzinfo=timezone.utc))
    assert len(result["downtime_windows"]) == 1
    assert result["downtime_windows"][0]["duration_minutes"] > 29


def test_availability_no_gap_when_events_are_close_together():
    events = [
        evt("2026-08-18T10:00:00Z", "vplus_input", "TXN1"),
        evt("2026-08-18T10:01:00Z", "vplus_input", "TXN2"),
        evt("2026-08-18T10:02:00Z", "vplus_input", "TXN3"),
    ]
    result = compute_vplus_availability(events, gap_threshold_minutes=10, reference_now=datetime(2026, 8, 18, 10, 5, tzinfo=timezone.utc))
    assert result["downtime_windows"] == []
    assert result["status"] == "healthy"


def test_availability_currently_down_when_last_event_too_old():
    events = [evt("2026-08-18T10:00:00Z", "vplus_input", "TXN1")]
    result = compute_vplus_availability(events, gap_threshold_minutes=10, reference_now=datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc))
    assert result["currently_down"] is True
    assert result["status"] == "down"


def test_availability_future_timestamped_event_clamped_not_negative():
    """Clock skew between a log source and the server can make an event's
    timestamp appear slightly after "now" -- this should read as
    essentially-current activity (0 minutes ago, healthy), not a confusing
    negative "-349m ago" figure."""
    events = [evt("2026-08-18T11:00:00Z", "vplus_input", "TXN1")]
    result = compute_vplus_availability(events, gap_threshold_minutes=10, reference_now=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc))
    assert result["minutes_since_last_event"] == 0.0
    assert result["currently_down"] is False
    assert result["status"] == "healthy"


def test_availability_ignores_non_vplus_components():
    events = [evt("2026-08-18T10:00:00Z", "sms_input", "TXN1"), evt("2026-08-18T10:30:00Z", "otp_success", "TXN1")]
    result = compute_vplus_availability(events)
    assert result["status"] == "no_data"


# -- response times ---------------------------------------------------------------

def test_response_time_pairs_input_and_response():
    events = [evt("2026-08-18T10:00:00Z", "vplus_input", "TXN1"), evt("2026-08-18T10:00:03Z", "vplus_response", "TXN1")]
    result = compute_response_time_stats(events, expected_response_ms=5000)
    assert result["total_pairs_analyzed"] == 1
    assert result["stats"]["avg_ms"] == 3000.0
    assert result["delayed_count"] == 0


def test_response_time_flags_delayed_pair():
    events = [evt("2026-08-18T10:00:00Z", "vplus_input", "TXN1"), evt("2026-08-18T10:00:10Z", "vplus_response", "TXN1")]
    result = compute_response_time_stats(events, expected_response_ms=5000)
    assert result["delayed_count"] == 1
    assert result["delayed_pct"] == 100.0


def test_response_time_no_pairs_returns_no_data():
    events = [evt("2026-08-18T10:00:00Z", "vplus_input", "TXN1")]  # no response
    result = compute_response_time_stats(events)
    assert result["status"] == "no_data"


def test_response_time_ignores_out_of_order_negative_delta():
    events = [evt("2026-08-18T10:00:10Z", "vplus_input", "TXN1"), evt("2026-08-18T10:00:00Z", "vplus_response", "TXN1")]
    result = compute_response_time_stats(events)
    assert result["status"] == "no_data"  # negative delta rejected, not reported as a fake -10s


# -- SMS analysis -------------------------------------------------------------

def test_sms_analysis_tracks_full_flow_to_confirmation():
    events = [
        evt("2026-08-18T10:00:00Z", "sms_input", "TXN1"),
        evt("2026-08-18T10:00:05Z", "sms_queue", "TXN1"),
        evt("2026-08-18T10:01:00Z", "otp_success", "TXN1"),
    ]
    result = compute_sms_analysis(events, expected_queue_ms=30000)
    assert result["total_sms_transactions"] == 1
    assert result["outcome_counts"]["otp_confirmed"] == 1
    assert result["queue_delay_stats"]["avg_ms"] == 5000.0


def test_sms_analysis_flags_unconfirmed_transaction():
    events = [evt("2026-08-18T10:00:00Z", "sms_input", "TXN1"), evt("2026-08-18T10:00:05Z", "sms_queue", "TXN1")]
    result = compute_sms_analysis(events)
    assert result["outcome_counts"]["queued_no_confirmation"] == 1
    assert len(result["unresolved"]) == 1


def test_sms_analysis_no_activity_returns_no_data():
    events = [evt("2026-08-18T10:00:00Z", "vplus_input", "TXN1")]
    result = compute_sms_analysis(events)
    assert result["status"] == "no_data"


def test_sms_analysis_includes_honest_aggregator_caveat():
    events = [evt("2026-08-18T10:00:00Z", "sms_input", "TXN1")]
    result = compute_sms_analysis(events)
    assert "no distinct sms-aggregator" in result["aggregator_note"].lower()


# -- investigation summary -----------------------------------------------------

def test_investigation_summary_flags_currently_down():
    avail = {"currently_down": True, "minutes_since_last_event": 45, "downtime_windows": []}
    rt = {"status": "no_data"}
    sms = {"status": "no_data"}
    summary = compute_investigation_summary([], avail, rt, sms)
    assert any("DOWN right now" in f["finding"] for f in summary["top_findings"])
    assert summary["top_findings"][0]["severity"] == "critical"


def test_investigation_summary_correlates_merchant_from_error_events():
    events = [
        evt("2026-08-18T10:00:00Z", "error", "TXN1", level="ERROR", message="timeout",
            tx={"merchant": {"name": "Acme"}, "issuer_id": "ISS1"}),
        evt("2026-08-18T10:00:01Z", "error", "TXN2", level="ERROR", message="timeout",
            tx={"merchant": {"name": "Acme"}, "issuer_id": "ISS1"}),
    ]
    avail = {"currently_down": False, "downtime_windows": []}
    rt = {"status": "no_data"}
    sms = {"status": "no_data"}
    summary = compute_investigation_summary(events, avail, rt, sms)
    assert summary["most_affected_merchants"]["Acme"] == 2
    assert any("Acme" in f["finding"] for f in summary["top_findings"])


def test_investigation_summary_severity_ordering():
    avail = {"currently_down": True, "minutes_since_last_event": 45, "downtime_windows": []}
    rt = {"status": "ok", "delayed_pct": 15, "expected_response_ms": 5000}
    sms = {"status": "no_data"}
    summary = compute_investigation_summary([], avail, rt, sms)
    severities = [f["severity"] for f in summary["top_findings"]]
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ranks = [severity_rank[s] for s in severities]
    assert ranks == sorted(ranks)  # critical/high before medium/low
