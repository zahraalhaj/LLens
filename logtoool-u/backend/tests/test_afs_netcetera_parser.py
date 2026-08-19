import pytest

from backend.core import custom_parser_registry as reg

SAMPLE_LOG = (
    '8/10/2026 2:14:10 PM Log Tracker No: SU12345 => Stepup Responce to Netcetra: '
    '{"TransactionId": "TXN001", "IssuerId": "ISS01", "ProcessorId": "PROC01", "Status": "SUCCESS", '
    '"MerchantInfo": {"MerchantName": "Acme Store", "MerchantId": "M001", "MerchantURL": "acme.com", '
    '"MerchantCountryCode": "840", "MerchantCategoryCode": "5411"}, '
    '"TransactionInfo": {"TransactionAmount": "100.00", "TransactionCurrency": "840"}, '
    '"Credentials": [{"Type": "SMS", "Text": "555-1234"}]}\n'
    '8/10/2026 2:14:20 PM Log Tracker No: IA67890 => Request Body: '
    '{"TransactionId": "TXN001", "StepupRequestId": "REQ001", "VerificationToken": "TOK123"}\n'
    '8/10/2026 2:14:30 PM Log Tracker No: IA67890 => OTP Processed Successfully for user\n'
    '8/10/2026 2:15:00 PM Log Tracker No: SU12345 => Connection timeout occurred while contacting Netcetera\n'
    'this is a completely unrelated line with no timestamp or tracker\n'
)


def test_afs_netcetera_detected():
    profile, warnings = reg.detect_custom_parser(SAMPLE_LOG)
    assert profile is not None
    assert profile.name == "AFS / Netcetera 3DS StepUp (Transaction Correlation)"


def test_no_false_positive_on_unrelated_text():
    profile, _ = reg.detect_custom_parser("just some ordinary log text\nwith no special format\n")
    assert profile is None


def test_run_custom_parser_links_events_by_transaction_id(tmp_path):
    profile = reg.get_custom_profile_by_name("AFS / Netcetera 3DS StepUp (Transaction Correlation)")
    log_path = tmp_path / "afs.log"
    log_path.write_text(SAMPLE_LOG)

    from datetime import datetime, timezone
    events = reg.run_custom_parser(
        profile=profile, file_path=str(log_path), batch_id="batch-1",
        file_name="afs.log", upload_time=datetime.now(timezone.utc),
    )

    # 4 matched lines + 1 failed-to-parse line, none discarded
    assert len(events) == 5

    su_event = next(e for e in events if "Netcetra" in e.raw or "Netcetra" in e.message)
    ia_events = [e for e in events if e.attributes["correlation_id"] == "TXN001" and e.component == "request_body"]

    # SU and IA tracker events resolve to the SAME correlation_id (the
    # transaction), even though they came from two different tracker numbers.
    assert su_event.attributes["correlation_id"] == "TXN001"
    assert len(ia_events) == 1

    # Transaction context (merchant/customer/derived) attached to events,
    # not just the raw per-line JSON.
    assert su_event.attributes["details"]["transaction"]["merchant"]["name"] == "Acme Store"
    assert su_event.attributes["details"]["transaction"]["customer"]["mobile"] == "555-1234"
    assert su_event.attributes["details"]["transaction"]["derived"]["is_success"] is True


def test_error_classification_maps_to_error_level(tmp_path):
    profile = reg.get_custom_profile_by_name("AFS / Netcetera 3DS StepUp (Transaction Correlation)")
    log_path = tmp_path / "afs.log"
    log_path.write_text(SAMPLE_LOG)

    from datetime import datetime, timezone
    events = reg.run_custom_parser(
        profile=profile, file_path=str(log_path), batch_id="batch-1",
        file_name="afs.log", upload_time=datetime.now(timezone.utc),
    )
    timeout_event = next(e for e in events if "timeout" in e.message.lower())
    assert timeout_event.level == "ERROR"
    assert timeout_event.component == "error"


def test_failed_line_surfaced_not_discarded(tmp_path):
    profile = reg.get_custom_profile_by_name("AFS / Netcetera 3DS StepUp (Transaction Correlation)")
    log_path = tmp_path / "afs.log"
    log_path.write_text(SAMPLE_LOG)

    from datetime import datetime, timezone
    events = reg.run_custom_parser(
        profile=profile, file_path=str(log_path), batch_id="batch-1",
        file_name="afs.log", upload_time=datetime.now(timezone.utc),
    )
    unparsed = [e for e in events if e.component == "unparsed"]
    assert len(unparsed) == 1
    assert unparsed[0].level == "WARN"
    assert unparsed[0].ts_confidence == "unparseable"


def test_full_ingestion_pipeline_routes_to_afs_netcetera(tmp_path):
    import io
    from backend.core.ingest import LogIngestionEngine
    from backend.core.profiles import ProfileManager
    from backend.core.store import DatabaseManager

    db = DatabaseManager(db_path=str(tmp_path / "test.db"))
    pm = ProfileManager(profiles_dir=str(tmp_path / "profiles"))
    engine = LogIngestionEngine(db_manager=db, profile_manager=pm)

    file_obj = io.BytesIO(SAMPLE_LOG.encode("utf-8"))
    summary = engine.ingest_file_stream(file_obj=file_obj, file_name="afs.log", file_size_bytes=len(SAMPLE_LOG))

    assert summary.matched_profile == "AFS / Netcetera 3DS StepUp (Transaction Correlation)"
    assert summary.parsed_events_count == 5

    events, total = db.query_events(page=1, page_size=10)
    assert total == 5
    assert all(e["source_system"] == "afs_netcetera_3ds_stepup" for e in events)
