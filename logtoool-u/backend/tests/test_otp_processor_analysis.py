from backend.analysis.otp_processor import compute_otp_summary


def _event(component, ts_utc, correlation_id=None, record=None, parse_error=None, message=""):
    details = {"tracker_no": correlation_id}
    if record is not None:
        details["record"] = record
    if parse_error is not None:
        details["parse_error"] = parse_error
    return {
        "ts_utc": ts_utc,
        "level": "WARN" if parse_error else "INFO",
        "component": component,
        "message": message,
        "attributes": {"correlation_id": correlation_id, "details": details},
    }


RECORD_A = {
    "org": "ABCBANK",
    "queue": "mq-oab-otp-in-push",
    "merchant": "Book & Bean",
    "transaction": {"currency": "USD"},
    "otp_processed": True,
    "force_verify_by_mobile": False,
}

RECORD_B = {
    "org": "XYZBANK",
    "queue": "mq-other-queue",
    "merchant": "Coffee Corner",
    "transaction": {"currency": None},
    "otp_processed": False,
    "force_verify_by_mobile": True,
}


def test_no_data_on_empty_events():
    result = compute_otp_summary([])
    assert result["status"] == "no_data"


def test_aggregates_by_queue_org_and_merchant():
    events = [
        _event("msg_received_sms_xml", "2026-08-10T14:14:10Z", "IA1", RECORD_A),
        _event("queue", "2026-08-10T14:14:11Z", "IA1", RECORD_A),
        _event("msg_received_sms_xml", "2026-08-11T09:00:00Z", "IA2", RECORD_B),
    ]
    result = compute_otp_summary(events)

    assert result["status"] == "ok"
    assert result["total_records"] == 2
    assert result["by_org"] == {"ABCBANK": 1, "XYZBANK": 1}
    assert result["by_queue"] == {"mq-oab-otp-in-push": 1, "mq-other-queue": 1}
    assert result["top_merchants"] == {"Book & Bean": 1, "Coffee Corner": 1}
    assert result["otp_processed_count"] == 1
    assert result["otp_success_rate_pct"] == 50.0
    assert result["force_verify_count"] == 1


def test_multiple_events_for_same_tracker_counted_once():
    events = [
        _event("msg_received_sms_xml", "2026-08-10T14:14:10Z", "IA1", RECORD_A),
        _event("queue", "2026-08-10T14:14:11Z", "IA1", RECORD_A),
        _event("otp_success", "2026-08-10T14:14:20Z", "IA1", RECORD_A),
    ]
    result = compute_otp_summary(events)
    assert result["total_records"] == 1
    assert result["by_org"] == {"ABCBANK": 1}


def test_failed_events_surfaced_with_reason_and_not_counted_as_a_record():
    events = [
        _event("msg_received_sms_xml", "2026-08-10T14:14:10Z", "IA1", RECORD_A),
        _event("other", "2026-08-10T14:14:30Z", None, parse_error="Unknown event type", message="garbled line"),
    ]
    result = compute_otp_summary(events)

    assert result["total_records"] == 1
    assert result["failed_events"]["count"] == 1
    assert result["failed_events"]["reason_counts"] == {"Unknown event type": 1}
    assert result["failed_events"]["items"][0]["message"] == "garbled line"


def test_window_bounds_reflect_ascending_event_order():
    events = [
        _event("msg_received_sms_xml", "2026-08-10T14:14:10Z", "IA1", RECORD_A),
        _event("msg_received_sms_xml", "2026-08-11T09:00:00Z", "IA2", RECORD_B),
    ]
    result = compute_otp_summary(events)
    assert result["window_start"] == "2026-08-10T14:14:10Z"
    assert result["window_end"] == "2026-08-11T09:00:00Z"
