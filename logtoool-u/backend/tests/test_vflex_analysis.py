from backend.analysis.vflex import compute_vflex_summary


def _event(component, ts_utc, correlation_id=None, transaction=None, level="INFO", message=""):
    details = {}
    if transaction is not None:
        details["transaction"] = transaction
    return {
        "ts_utc": ts_utc,
        "level": level,
        "component": component,
        "message": message,
        "attributes": {"correlation_id": correlation_id, "details": details},
    }


TX_A = {
    "issuer_id": "ISS1",
    "status": None,
    "integrity_status": "OK",
    "merchant": {"name": "Store A"},
    "bank_api": {"operation": "OTP_REQUEST", "success": True},
    "otp": {"channel": "SMS", "processed_successfully": True},
}

TX_B = {
    "issuer_id": "ISS2",
    "status": None,
    "integrity_status": "CHECK",
    "merchant": {"name": "Store B"},
    "bank_api": {"operation": "SETUP_REQUEST", "success": False},
    "otp": {"channel": None, "processed_successfully": False},
}


def test_no_data_on_empty_events():
    assert compute_vflex_summary([])["status"] == "no_data"


def test_aggregates_by_issuer_bank_operation_and_channel():
    events = [
        _event("bank_api_success_response", "2026-08-17T15:00:00Z", "IA1", TX_A),
        _event("bank_api_error_response", "2026-08-17T15:00:05Z", "IA2", TX_B),
    ]
    result = compute_vflex_summary(events)

    assert result["status"] == "ok"
    assert result["total_records"] == 2
    assert result["by_issuer"] == {"ISS1": 1, "ISS2": 1}
    assert result["by_bank_operation"] == {"OTP_REQUEST": 1, "SETUP_REQUEST": 1}
    assert result["by_channel"] == {"SMS": 1, "UNKNOWN": 1}
    assert result["top_merchants"] == {"Store A": 1, "Store B": 1}
    assert result["otp_processed_count"] == 1
    assert result["bank_api_success_count"] == 1
    assert result["checks_needed_count"] == 1


def test_error_level_event_surfaced_as_failed_not_dropped():
    events = [
        _event("bank_api_success_response", "2026-08-17T15:00:00Z", "IA1", TX_A),
        _event("error", "2026-08-17T15:00:05Z", "IA1", level="ERROR", message="host unreachable"),
    ]
    result = compute_vflex_summary(events)

    assert result["failed_events"]["count"] == 1
    assert result["failed_events"]["items"][0]["message"] == "host unreachable"
    assert result["total_records"] == 1
