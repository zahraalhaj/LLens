import io
import pytest

from backend.core import custom_parser_registry as reg


ABCE_CREDIT_SAMPLE = """8/5/2026 2:14:10 PM Log Tracker No: TRK001 => CC_AccountEnquiry Inputs-:Data in CC_AccountEnquiry(OrgNo=100, CIF=5001, AccountNo=12345)
8/5/2026 2:15:00 PM Log Tracker No: TRK002 => <?xml version="1.0"?><Iso8583PostXml><MsgType>0200</MsgType><Fields><F2>4111</F2></Fields></Iso8583PostXml>
8/5/2026 2:16:00 PM Log Tracker No: TRK003 => Warrning: connection pool nearly exhausted
"""

ASBB_MW_CREDIT_SAMPLE = (
    "2026-08-05 14:14:10.123 +00:00 [INFO] Log Tracker No: TRK900 => Inputs : CC_ (OrgNo=200,TranReference=555,CIF=9001)\n"
    "2026-08-05 14:15:00.456 +00:00 [WARN] Log Tracker No: TRK901 => Warrning: cache miss rate high\n"
)

ABCE_DEBIT_SAMPLE = """8/5/2026 2:14:10 PM -> 0---Inputs in GetCardListByCustomerId(CustomerId=778, CardType=DEBIT)
8/5/2026 2:15:00 PM -> 1---New Request received for step validation
"""

ASBB_DEBIT_SAMPLE = """8/5/2026 2:14:10 PM -> 500---Inputs in GetCardListByCustomerId(CustomerId=42)
"""


def test_asbb_mw_credit_detected_first_due_to_unique_iso_timestamp():
    profile, warnings = reg.detect_custom_parser(ASBB_MW_CREDIT_SAMPLE)
    assert profile is not None
    assert profile.name == "ASBB MW Credit Portal (ISO Timestamp + Log Tracker)"
    assert warnings == []


def test_abce_credit_detected_via_log_tracker_and_no_arrow():
    profile, warnings = reg.detect_custom_parser(ABCE_CREDIT_SAMPLE)
    assert profile is not None
    assert profile.name == "ABCE Credit Portal (Log Tracker / ISO8583 XML)"


def test_debit_family_ambiguity_is_surfaced_as_a_warning():
    # ABCE_Debit and ASBB_Debit are genuinely indistinguishable from content
    # alone -- both should match, and the ambiguity must be reported, not
    # silently resolved as if it were certain.
    profile, warnings = reg.detect_custom_parser(ASBB_DEBIT_SAMPLE)
    assert profile is not None
    assert profile.type == "custom"
    assert len(warnings) == 1
    assert "Multiple custom parsers matched" in warnings[0]


def test_no_custom_parser_matches_unrelated_text():
    profile, warnings = reg.detect_custom_parser("just some ordinary unrelated text\nwith no special format\n")
    assert profile is None
    assert warnings == []


def test_run_custom_parser_maps_xml_payload_record(tmp_path):
    profile = reg.get_custom_profile_by_name("ABCE Credit Portal (Log Tracker / ISO8583 XML)")
    log_path = tmp_path / "abce_credit.log"
    log_path.write_text(ABCE_CREDIT_SAMPLE)

    from datetime import datetime, timezone
    events = reg.run_custom_parser(
        profile=profile,
        file_path=str(log_path),
        batch_id="batch-1",
        file_name="abce_credit.log",
        upload_time=datetime.now(timezone.utc),
    )

    assert len(events) == 3
    assert events[0].component == "FUNCTION_INPUT"
    assert events[0].attributes["correlation_id"] == "TRK001"
    assert events[0].attributes["details"]["parameters"]["CIF"] == "5001"

    assert events[1].component == "XML_PAYLOAD"
    assert events[1].attributes["details"]["msg_type"] == "0200"

    assert events[2].component == "WARNING"
    assert events[2].level == "WARN"


def test_run_custom_parser_uses_real_level_when_present(tmp_path):
    profile = reg.get_custom_profile_by_name("ASBB MW Credit Portal (ISO Timestamp + Log Tracker)")
    log_path = tmp_path / "asbb_mw.log"
    log_path.write_text(ASBB_MW_CREDIT_SAMPLE)

    from datetime import datetime, timezone
    events = reg.run_custom_parser(
        profile=profile,
        file_path=str(log_path),
        batch_id="batch-2",
        file_name="asbb_mw.log",
        upload_time=datetime.now(timezone.utc),
    )
    assert len(events) == 2
    assert events[0].level == "INFO"
    assert events[1].level == "WARN"  # real [WARN] from the log, not derived from log_type


def test_full_ingestion_pipeline_routes_to_custom_parser(tmp_path):
    """End-to-end: LogIngestionEngine, given a file that only a custom
    parser recognizes, should auto-detect and use it -- not silently fall
    back to a wrong declarative profile."""
    from backend.core.ingest import LogIngestionEngine
    from backend.core.profiles import ProfileManager
    from backend.core.store import DatabaseManager

    db = DatabaseManager(db_path=str(tmp_path / "test.db"))
    pm = ProfileManager(profiles_dir=str(tmp_path / "profiles"))
    engine = LogIngestionEngine(db_manager=db, profile_manager=pm)

    file_obj = io.BytesIO(ASBB_MW_CREDIT_SAMPLE.encode("utf-8"))
    summary = engine.ingest_file_stream(
        file_obj=file_obj, file_name="asbb_mw.log", file_size_bytes=len(ASBB_MW_CREDIT_SAMPLE)
    )

    assert summary.matched_profile == "ASBB MW Credit Portal (ISO Timestamp + Log Tracker)"
    assert summary.parsed_events_count == 2

    events, total = db.query_events(page=1, page_size=10)
    assert total == 2
    assert events[0]["source_system"] == "asbb_mw_credit_portal"
