"""
Tests for the Phase 2 normalized event layer:
backend/analysis/normalized_schema.py + backend/analysis/normalize.py +
each family's normalize_<family>_event() (added to the existing
backend/analysis/{cardinal,vplus_monitoring,debit_portal,vflex,otp_processor}.py
modules).

Covers, per the Phase 2 spec: valid events, malformed events, partial
events, missing identifiers, duplicate events, sensitive-field handling,
timestamp parsing, and parser failures -- grouped by family, plus a
dispatcher-level section for cross-family concerns (unknown source_system,
never-raises guarantee).
"""
from backend.analysis.normalize import normalize_event, normalize_events
from backend.analysis.normalized_schema import (
    LogFamily,
    extract_card_last4,
    mask_email,
    mask_mobile,
)


def _base_event(source_system, **overrides):
    event = {
        "event_id": "evt-1",
        "batch_id": "batch-1",
        "file_name": "sample.log",
        "line_no": 1,
        "ts_utc": "2026-08-20T10:00:00Z",
        "ts_raw": "2026-08-20 10:00:00",
        "level": "INFO",
        "source_system": source_system,
        "component": "some_event_type",
        "message": "a message",
        "raw": "the raw text",
        "attributes": {"correlation_id": None, "details": {}},
    }
    event.update(overrides)
    return event


# ---------------------------------------------------------------------------
# Shared masking helpers
# ---------------------------------------------------------------------------

def test_mask_mobile_keeps_only_last_two_digits():
    assert mask_mobile("+15551234567") == "*********67"


def test_mask_mobile_handles_none_and_empty():
    assert mask_mobile(None) is None
    assert mask_mobile("") is None


def test_mask_email_keeps_first_char_and_domain():
    assert mask_email("jane.doe@example.com") == "j***@example.com"


def test_mask_email_rejects_value_without_at_sign():
    assert mask_email("not-an-email") is None


def test_extract_card_last4_anchors_to_end_not_first_digit_run():
    # A malformed masked value with a digit run BEFORE the real last4 must
    # not have that earlier group mistaken for the last4.
    assert extract_card_last4("4111 masked 9999") == "9999"


def test_extract_card_last4_returns_none_when_no_trailing_digits():
    assert extract_card_last4("no digits here") is None
    assert extract_card_last4(None, "") is None


# ---------------------------------------------------------------------------
# Dispatcher-level behavior
# ---------------------------------------------------------------------------

def test_unknown_source_system_returns_generic_event_not_none():
    """A source_system with no registered family normalizer (a
    declarative profile, or one of the 4 custom parsers outside the 5
    named families) now gets a minimal LogFamily.GENERIC NormalizedEvent
    instead of being dropped -- see normalize_event()'s docstring."""
    event = _base_event("some_declarative_profile_not_a_payment_family")
    result = normalize_event(event)
    assert result is not None
    assert result.log_family == LogFamily.GENERIC


def test_normalize_events_keeps_both_generic_and_registered_sources():
    events = [
        _base_event("some_declarative_profile_not_a_payment_family"),
        _base_event(LogFamily.OTP_PROCESSOR.value),
    ]
    result = normalize_events(events)
    assert len(result) == 2
    families = {e.log_family for e in result}
    assert families == {LogFamily.GENERIC, LogFamily.OTP_PROCESSOR}


def test_generic_event_extracts_correlation_id_from_attributes():
    """Every custom parser (including the 4 outside the 5 named families)
    already writes attributes.correlation_id by convention -- the generic
    fallback should pick that up for free, with no correlation_keys
    configuration needed."""
    event = _base_event(
        "some_custom_parser_not_in_log_family",
        attributes={"correlation_id": "TXN-GENERIC-1", "details": {}},
    )
    result = normalize_event(event)
    assert result.extra_identifiers.get("correlation_id") == "TXN-GENERIC-1"


def test_generic_event_extracts_declared_correlation_keys():
    event = _base_event(
        "some_declarative_profile",
        attributes={"session_id": "SESS-1", "unrelated_field": "x"},
    )
    result = normalize_event(event, correlation_keys_by_source={"some_declarative_profile": ["session_id"]})
    assert result.extra_identifiers.get("session_id") == "SESS-1"
    assert "unrelated_field" not in result.extra_identifiers


def test_generic_event_with_no_correlation_signal_has_empty_extra_identifiers():
    event = _base_event("some_declarative_profile", attributes={})
    result = normalize_event(event)
    assert result.extra_identifiers == {}


def test_dispatcher_never_raises_on_malformed_details_shape():
    """Every field access inside a family extractor uses .get()-style safe
    lookups, but this is the backstop: if a family's `details` shape is
    something totally unexpected (e.g. a list instead of a dict), the
    dispatcher must degrade to a minimal NormalizedEvent, not raise."""
    event = _base_event(
        LogFamily.CARDINAL.value,
        attributes={"correlation_id": "X", "details": {"flow": "not-a-dict", "normalized_payloads": "also-not-a-list"}},
    )
    result = normalize_event(event)
    assert result is not None
    assert result.parse_status == "failed"
    assert result.evidence_level == "minimal"
    assert result.log_family == LogFamily.CARDINAL


def test_duplicate_events_normalize_independently_and_deterministically():
    """Normalizing the same canonical event twice must produce identical
    output -- normalization is a pure function of its input, no hidden
    state/counters that would make event #2 differ from event #1."""
    event = _base_event(
        LogFamily.OTP_PROCESSOR.value,
        attributes={"correlation_id": "IA123", "details": {"tracker_no": "IA123", "record": {"tracker_no": "IA123", "org": "ORGX"}}},
    )
    first = normalize_event(event)
    second = normalize_event(dict(event))
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# Cardinal
# ---------------------------------------------------------------------------

def _cardinal_event(**overrides):
    overrides.setdefault("component", "vplus_response")
    return _base_event(LogFamily.CARDINAL.value, **overrides)


def test_cardinal_valid_event_full_flow():
    event = _cardinal_event(
        attributes={
            "correlation_id": "TXN123",
            "details": {
                "phase": "STEP_UP",
                "parse_status": "parsed",
                "identifiers": {"oob_tracker_id": "OOB1", "stepup_request_ids": ["SREQ1"]},
                "http": {"status_code": 200, "urls": ["https://api.example.com/x"]},
                "queue": {"names": ["q1"], "message_id": "M1"},
                "normalized_payloads": [
                    {
                        "transaction_id": "TXN123",
                        "credentials": {"items": [{"id": "CRED1", "type": "SMS"}], "oob_credential_id": "CRED1"},
                    }
                ],
                "flow": {
                    "transaction_id": "TXN123",
                    "trackers": ["SU99887"],
                    "stepup_request_ids": ["SREQ1"],
                    "issuer_id": "ISS1",
                    "bank_org": "BANKX",
                    "authentication": {"type": "OTP", "status": "SUCCESS", "verification_token": "secret", "otp_reference_code": "REF001"},
                    "customer": {"mobile": "+15551234567", "email": "jane@example.com"},
                    "merchant": {"id": "M1", "name": "Acme"},
                    "transaction": {"amount": 42.5, "currency": "USD"},
                    "payment": {"card_number": "4111111111111111"},
                    "oob": {"status_history": ["PENDING", "SUCCESS"], "card_blocked_history": [False]},
                    "integrity_status": "OK",
                },
            },
        }
    )
    result = normalize_event(event)

    assert result.log_family == LogFamily.CARDINAL
    assert result.tracker_no == "SU99887"
    assert result.tracker_type == "SU"
    assert result.transaction_id == "TXN123"
    assert result.ds_transaction_id == "TXN123"
    assert result.stepup_request_id == "SREQ1"
    assert result.credential_id == "CRED1"
    assert result.issuer_id == "ISS1"
    assert result.bank_org == "BANKX"
    assert result.merchant_name == "Acme"
    assert result.amount == 42.5
    assert result.currency == "USD"
    assert result.card_last4 == "1111"
    assert result.masked_mobile == "*********67"
    assert result.masked_email == "j***@example.com"
    assert result.verification_token_present is True
    assert result.otp_reference_code == "REF001"
    assert result.oob_status == "SUCCESS"
    assert result.card_blocked is False
    assert result.terminal_status == "OK"
    assert result.parse_status == "parsed"
    assert result.correlation_confidence == 1.0
    assert result.evidence_level == "full"


def test_cardinal_never_stores_raw_card_number_verification_token_or_contact_info():
    event = _cardinal_event(
        attributes={
            "correlation_id": "TXN1",
            "details": {
                "flow": {
                    "transaction_id": "TXN1",
                    "authentication": {"verification_token": "super-secret-token"},
                    "customer": {"mobile": "+15551234567", "email": "jane@example.com"},
                    "payment": {"card_number": "4111111111111111"},
                }
            },
        }
    )
    result = normalize_event(event)
    dumped = result.model_dump_json()

    assert "4111111111111111" not in dumped
    assert "super-secret-token" not in dumped
    assert "+15551234567" not in dumped
    assert "jane@example.com" not in dumped
    assert result.verification_token_present is True
    assert set(result.sensitive_fields_removed) == {
        "payment.card_number",
        "customer.mobile",
        "customer.email",
        "authentication.verification_token",
    }


def test_cardinal_missing_identifiers_falls_back_gracefully():
    """No flow, no normalized_payloads, no correlation_id at all -- every
    identifier field should resolve to None, not raise."""
    event = _cardinal_event(attributes={"correlation_id": None, "details": {}})
    result = normalize_event(event)

    assert result.tracker_no is None
    assert result.transaction_id is None
    assert result.stepup_request_id is None
    assert result.correlation_confidence == 0.2
    assert result.parse_status == "parsed"  # missing identifiers isn't itself a parse failure


def test_cardinal_partial_parse_status_produces_failure_signature():
    event = _cardinal_event(
        component="oob_http_error",
        level="ERROR",
        message="Host unreachable",
        attributes={
            "correlation_id": "SU1",
            "details": {"parse_status": "partial", "warnings": ["Tracker number was not found"]},
        },
    )
    result = normalize_event(event)

    assert result.parse_status == "partial"
    assert result.failure_signature == "cardinal_stepup_oob_log:oob_http_error:tracker_number_was_not_found"
    assert result.normalized_stage == "ERROR"


def test_cardinal_tracker_fallback_from_correlation_id_when_no_flow():
    """When no flow/identifiers resolved a tracker, but correlation_id
    itself looks tracker-shaped, use it -- this is the same fallback every
    other family extractor applies."""
    event = _cardinal_event(attributes={"correlation_id": "IA555", "details": {}})
    result = normalize_event(event)
    assert result.tracker_no == "IA555"
    assert result.tracker_type == "IA"
    assert result.phase == "INITIATE_ACTION"


# ---------------------------------------------------------------------------
# Netcetera / V+
# ---------------------------------------------------------------------------

def _netcetera_event(**overrides):
    overrides.setdefault("component", "stepup_message")
    return _base_event(LogFamily.NETCETERA_VPLUS.value, **overrides)


def test_netcetera_valid_event():
    event = _netcetera_event(
        attributes={
            "correlation_id": "TXN9",
            "details": {
                "tracker_no": "SU12345",
                "tracker_type": "SU",
                "msg_id": "MSG1",
                "transaction": {
                    "transaction_id": "TXN9",
                    "issuer_id": "ISS9",
                    "merchant": {"id": "MER1", "name": "Shop9"},
                    "transaction_info": {"amount": 10.0, "currency": "EUR"},
                    "customer": {"mobile": "+447911123456", "email": "a@b.com"},
                    "derived": {"has_stepup": True, "has_sms": True, "is_success": True},
                    "stepup_status": "SUCCESS",
                },
            },
        }
    )
    result = normalize_event(event)

    assert result.log_family == LogFamily.NETCETERA_VPLUS
    assert result.tracker_no == "SU12345"
    assert result.transaction_id == "TXN9"
    assert result.ds_transaction_id == "TXN9"
    assert result.issuer_id == "ISS9"
    assert result.merchant_name == "Shop9"
    assert result.amount == 10.0
    assert result.currency == "EUR"
    assert result.authentication_method == "STEPUP"
    assert result.channel == "SMS"
    assert result.terminal_status == "SUCCESS"
    assert result.masked_mobile is not None and "447911123456" not in result.masked_mobile
    assert result.card_last4 is None  # documented limitation: not exposed by this family's adapter snapshot
    assert result.correlation_confidence == 1.0


def test_netcetera_unparsed_line_is_a_parser_failure_not_a_dropped_event():
    event = _netcetera_event(
        component="unparsed",
        level="WARN",
        message="No tracker number pattern matched",
        attributes={
            "correlation_id": None,
            "details": {"raw": "garbled line contents", "line_no": 42},
        },
    )
    result = normalize_event(event)

    assert result.parse_status == "failed"
    assert result.physical_line_start == 42  # the one case a true physical line number survives to this layer
    assert result.failure_signature is not None
    assert result.tracker_no is None


def test_netcetera_missing_transaction_context():
    event = _netcetera_event(attributes={"correlation_id": None, "details": {"tracker_no": "SU777"}})
    result = normalize_event(event)
    assert result.tracker_no == "SU777"
    assert result.transaction_id is None
    assert result.correlation_confidence == 0.5
    assert result.parse_status == "parsed"


# ---------------------------------------------------------------------------
# Debit Portal
# ---------------------------------------------------------------------------

def _debit_event(**overrides):
    overrides.setdefault("component", "debit_response")
    return _base_event(LogFamily.DEBIT_PORTAL.value, **overrides)


def test_debit_portal_valid_event():
    event = _debit_event(
        attributes={
            "correlation_id": "TXND1",
            "details": {
                "parse_status": "parsed",
                "parsed": {"masked_card": "411111XXXXXX2222"},
                "transaction": {
                    "transaction_id": "TXND1",
                    "trackers": ["IA1001"],
                    "issuer_id": "ISSD1",
                    "customer": {"mobile": "0999888777", "email": "d@e.com"},
                    "merchant": {"id": "MD1", "name": "DebitShop"},
                    "transaction": {"amount": 5.5, "currency": "GBP"},
                    "otp_processed": True,
                    "integrity_status": "OK",
                },
            },
        }
    )
    result = normalize_event(event)

    assert result.log_family == LogFamily.DEBIT_PORTAL
    assert result.tracker_no == "IA1001"
    assert result.transaction_id == "TXND1"
    assert result.ds_transaction_id is None  # not a 3DS family
    assert result.issuer_id == "ISSD1"
    assert result.merchant_name == "DebitShop"
    assert result.amount == 5.5
    assert result.currency == "GBP"
    assert result.card_last4 == "2222"
    assert result.authentication_method == "OTP"
    assert result.terminal_status == "OK"
    assert result.correlation_confidence == 1.0


def test_debit_portal_partial_event_with_parse_warning():
    event = _debit_event(
        component="parse_error",
        level="WARN",
        attributes={
            "correlation_id": None,
            "details": {"parse_status": "partial", "parse_warning": "Unrecognized XML structure"},
        },
    )
    result = normalize_event(event)

    assert result.parse_status == "partial"
    assert result.failure_signature == "debit_portal_log:parse_error:unrecognized_xml_structure"


def test_debit_portal_sensitive_fields_never_leak():
    event = _debit_event(
        attributes={
            "correlation_id": "T1",
            "details": {
                "parsed": {"mobile": "0999888777", "otp": "123456", "otppan": "999999999999", "masked_card": "XXXX1234"},
            },
        }
    )
    result = normalize_event(event)
    dumped = result.model_dump_json()

    assert "123456" not in dumped
    assert "999999999999" not in dumped
    assert "0999888777" not in dumped
    assert set(result.sensitive_fields_removed) == {"mobile", "parsed.otp", "parsed.otppan"}


# ---------------------------------------------------------------------------
# VFlex
# ---------------------------------------------------------------------------

def _vflex_event(**overrides):
    overrides.setdefault("component", "bank_api_response")
    return _base_event(LogFamily.VFLEX.value, **overrides)


def test_vflex_valid_event():
    event = _vflex_event(
        attributes={
            "correlation_id": "TXNV1",
            "details": {
                "parse_status": "parsed",
                "transaction": {
                    "transaction_id": "TXNV1",
                    "tracker_no": "SU5001",
                    "tracker_type": "SU",
                    "phase": "STEP_UP",
                    "issuer_id": "ISSV1",
                    "stepup_request_id": "SREQV1",
                    "status": "SUCCESS",
                    "verification_token": "tok",
                    "customer": {"mobile": "+15005550001", "email": "v@f.com", "client_customer_id": "CUSTV1"},
                    "merchant": {"name": "VShop"},
                    "transaction": {"amount": 99.99, "currency": "USD"},
                    "payment": {"last4_pan": "4321", "masked_card": None},
                    "bank_api": {"url": "https://bank.example/api", "operation": "AUTH", "transaction_reference": "REF-1", "org_number": "ORG1"},
                    "otp": {"channel": "SMS", "processed_successfully": True, "value": "654321"},
                    "queue": {"name": "q-vflex", "message_id": "QM1"},
                    "integrity_status": "OK",
                },
            },
        }
    )
    result = normalize_event(event)

    assert result.log_family == LogFamily.VFLEX
    assert result.tracker_no == "SU5001"
    assert result.transaction_id == "TXNV1"
    assert result.stepup_request_id == "SREQV1"
    assert result.tran_ref == "REF-1"
    assert result.endpoint == "https://bank.example/api"
    assert result.dependency_name == "bank_api"
    assert result.card_last4 == "4321"
    assert result.channel == "SMS"
    assert result.customer_id == "CUSTV1"
    assert result.verification_token_present is True
    assert result.correlation_confidence == 1.0
    assert "654321" not in result.model_dump_json()
    assert "otp.value" in result.sensitive_fields_removed


def test_vflex_missing_identifiers_and_empty_transaction():
    event = _vflex_event(attributes={"correlation_id": None, "details": {}})
    result = normalize_event(event)
    assert result.tracker_no is None
    assert result.correlation_confidence == 0.2
    assert result.parse_status == "parsed"


def test_vflex_error_event_with_reference_number_as_business_error_code():
    event = _vflex_event(
        component="bank_api_error",
        level="ERROR",
        attributes={
            "correlation_id": "SU9",
            "details": {
                "parse_status": "parsed",
                "transaction": {"error": {"reference_number": "ERR-42", "description": "Bank timeout"}},
            },
        },
    )
    result = normalize_event(event)
    assert result.business_error_code == "ERR-42"
    assert result.failure_signature == "vflex_transaction_log:bank_api_error:bank_timeout"


# ---------------------------------------------------------------------------
# OTP Online Processor
# ---------------------------------------------------------------------------

def _otp_event(**overrides):
    overrides.setdefault("component", "msg_received_sms_xml")
    return _base_event(LogFamily.OTP_PROCESSOR.value, **overrides)


def test_otp_processor_valid_event():
    event = _otp_event(
        attributes={
            "correlation_id": "IA7001",
            "details": {
                "tracker_no": "IA7001",
                "record": {
                    "tracker_no": "IA7001",
                    "org": "ORGX",
                    "mobile": "+15559990000",
                    "otp": "445566",
                    "masked_card": "XXXXXXXXXXXX7890",
                    "merchant": "OtpMerchant",
                    "merchant_details": {"id": "OM1"},
                    "transaction": {"amount": 3.25, "currency": "USD"},
                    "sms_msg_id": "SMSMSG1",
                    "otp_processed": True,
                    "queue": "otp-queue",
                },
            },
        }
    )
    result = normalize_event(event)

    assert result.log_family == LogFamily.OTP_PROCESSOR
    assert result.tracker_no == "IA7001"
    assert result.tracker_type == "IA"
    assert result.phase == "INITIATE_ACTION"
    assert result.bank_org == "ORGX"
    assert result.merchant_name == "OtpMerchant"
    assert result.merchant_id == "OM1"
    assert result.amount == 3.25
    assert result.card_last4 == "7890"
    assert result.channel == "SMS"
    assert result.authentication_method == "OTP"
    assert result.otp_reference_code == "SMSMSG1"
    assert result.terminal_status == "PROCESSED"
    assert result.queue_name == "otp-queue"
    assert result.correlation_confidence == 0.5
    assert "445566" not in result.model_dump_json()
    assert "+15559990000" not in result.model_dump_json()
    assert set(result.sensitive_fields_removed) == {"record.mobile", "record.otp"}


def test_otp_processor_parser_failure_event():
    event = _otp_event(
        component="other",
        level="WARN",
        attributes={
            "correlation_id": None,
            "details": {"tracker_no": None, "parse_error": "Unknown event type"},
        },
    )
    result = normalize_event(event)

    assert result.parse_status == "failed"
    assert result.failure_signature == "otp_online_processor:other:unknown_event_type"
    assert result.tracker_no is None
    assert result.correlation_confidence == 0.2


def test_otp_processor_missing_record_context():
    event = _otp_event(attributes={"correlation_id": "IA1", "details": {"tracker_no": "IA1"}})
    result = normalize_event(event)
    assert result.tracker_no == "IA1"
    assert result.amount is None
    assert result.merchant_name is None
    assert result.parse_status == "parsed"


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------

def test_event_timestamp_passes_through_resolved_ts_utc():
    event = _cardinal_event(ts_utc="2026-08-20T10:00:00Z")
    result = normalize_event(event)
    assert result.event_timestamp == "2026-08-20T10:00:00Z"


def test_event_timestamp_is_none_when_unparseable():
    """Timestamp parsing itself is core/timezones.py's job (upstream, at
    ingestion time) -- normalization just passes through whatever ts_utc
    the canonical event already has, including None for an unparseable
    source timestamp, rather than inventing one."""
    event = _cardinal_event(ts_utc=None)
    result = normalize_event(event)
    assert result.event_timestamp is None
    assert result.parse_status == "parsed"  # a missing timestamp alone isn't a normalization failure
