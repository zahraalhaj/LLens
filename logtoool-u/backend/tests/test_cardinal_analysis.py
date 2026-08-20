from backend.analysis.cardinal import compute_cardinal_summary


def _event(component, ts_utc, correlation_id=None, flow=None, level="INFO", message=""):
    details = {}
    if flow is not None:
        details["flow"] = flow
    return {
        "ts_utc": ts_utc,
        "level": level,
        "component": component,
        "message": message,
        "attributes": {"correlation_id": correlation_id, "details": details},
    }


FLOW_A = {
    "issuer_id": "ISS1",
    "bank_org": "BANKA",
    "authentication": {"status": "SUCCESS", "otp_processed": True},
    "merchant": {"name": "Store A"},
    "oob": {"status_history": ["PENDING", "SUCCESS"]},
    "integrity_status": "OK",
}

FLOW_B = {
    "issuer_id": "ISS2",
    "bank_org": None,
    "authentication": {"status": None, "otp_processed": False},
    "merchant": {"name": "Store B"},
    "oob": {"status_history": ["PENDING"]},
    "integrity_status": "CHECK",
}


def test_no_data_on_empty_events():
    assert compute_cardinal_summary([])["status"] == "no_data"


def test_aggregates_by_issuer_status_bank_org_and_oob():
    events = [
        _event("cardinal_stepup_response", "2026-08-17T15:00:00Z", "TXN1", FLOW_A),
        _event("oob_status_poll", "2026-08-17T15:00:05Z", "TXN2", FLOW_B),
    ]
    result = compute_cardinal_summary(events)

    assert result["status"] == "ok"
    assert result["total_flows"] == 2
    assert result["by_issuer"] == {"ISS1": 1, "ISS2": 1}
    assert result["by_status"] == {"SUCCESS": 1, "CHECK": 1}
    assert result["by_bank_org"] == {"BANKA": 1, "UNKNOWN": 1}
    assert result["oob_status_counts"] == {"PENDING": 2, "SUCCESS": 1}
    assert result["top_merchants"] == {"Store A": 1, "Store B": 1}
    assert result["otp_processed_count"] == 1
    assert result["checks_needed_count"] == 1


def test_error_level_event_surfaced_as_failed_not_dropped():
    events = [
        _event("cardinal_stepup_response", "2026-08-17T15:00:00Z", "TXN1", FLOW_A),
        _event("vplus_mq_timeout", "2026-08-17T15:00:05Z", "TXN1", level="ERROR", message="MQ Timeout"),
    ]
    result = compute_cardinal_summary(events)

    assert result["failed_events"]["count"] == 1
    assert result["failed_events"]["items"][0]["message"] == "MQ Timeout"
    assert result["total_flows"] == 1
