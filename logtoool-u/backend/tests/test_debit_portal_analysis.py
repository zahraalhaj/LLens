from backend.analysis.debit_portal import compute_debit_portal_summary


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
    "status": "SUCCESS",
    "merchant": {"name": "Store A"},
    "transaction": {"currency": "840"},
    "otp_processed": True,
    "integrity_status": "OK",
}

TX_B = {
    "issuer_id": "ISS2",
    "status": None,
    "merchant": {"name": "Store B"},
    "transaction": {"currency": None},
    "otp_processed": False,
    "integrity_status": "CHECK",
}


def test_no_data_on_empty_events():
    assert compute_debit_portal_summary([])["status"] == "no_data"


def test_aggregates_by_issuer_status_and_merchant():
    events = [
        _event("debit_request_json", "2026-08-17T15:00:00Z", "TXN1", TX_A),
        _event("debit_response_json", "2026-08-17T15:00:05Z", "TXN2", TX_B),
    ]
    result = compute_debit_portal_summary(events)

    assert result["status"] == "ok"
    assert result["total_records"] == 2
    assert result["by_issuer"] == {"ISS1": 1, "ISS2": 1}
    assert result["by_status"] == {"SUCCESS": 1, "CHECK": 1}
    assert result["top_merchants"] == {"Store A": 1, "Store B": 1}
    assert result["otp_processed_count"] == 1
    assert result["checks_needed_count"] == 1


def test_multiple_events_for_same_correlation_counted_once():
    events = [
        _event("debit_request_json", "2026-08-17T15:00:00Z", "TXN1", TX_A),
        _event("queue", "2026-08-17T15:00:01Z", "TXN1", TX_A),
    ]
    result = compute_debit_portal_summary(events)
    assert result["total_records"] == 1


def test_error_level_event_surfaced_as_failed_not_dropped():
    events = [
        _event("debit_request_json", "2026-08-17T15:00:00Z", "TXN1", TX_A),
        _event("error", "2026-08-17T15:00:05Z", "TXN1", level="ERROR", message="timeout contacting bank"),
    ]
    result = compute_debit_portal_summary(events)

    assert result["failed_events"]["count"] == 1
    assert result["failed_events"]["items"][0]["message"] == "timeout contacting bank"
    # the error event carries no `transaction` snapshot, so it must not
    # inflate total_records beyond the one resolved transaction
    assert result["total_records"] == 1
