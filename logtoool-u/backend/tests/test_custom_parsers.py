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


# --- ILA Bank application log parser -----------------------------------------
# Serilog-style abbreviated levels, a multi-line stack trace, an embedded JSON
# payload and an explicit duration -- none of which the ASBB MW parser (which
# claims the same ISO-timestamp header shape) has a branch for.
ILA_BANK_SAMPLE = (
    "2026-08-27 09:10:02.451 +00:00 [INF] Log Tracker No: TRK-9001 => "
    "PaymentService: begin posting, ReqRef=REQ-77, TranReference=778451, host=10.20.30.40:8443\n"
    "2026-08-27 09:10:02.900 +00:00 [ERR] Log Tracker No: TRK-9001 => "
    "Core adapter call failed: System.Net.Http.HttpRequestException (500) Internal Server Error\n"
    "   at Afs.Core.Adapter.PostAsync(String url) in C:\\src\\Adapter.cs:line 142\n"
    "2026-08-27 09:10:03.120 +00:00 [INF] Log Tracker No: TRK-9001 => "
    'Retry 1 scheduled, payload {"amount": 500.0, "currency": "USD"}\n'
    "2026-08-27 09:10:04.000 +00:00 [INF] End of request, duration 00:00:01.549\n"
)

ILA_BANK_NAME = "ILA Bank Application Log (Tracker + Byte-Exact Capture)"


def test_ila_bank_wins_iso_bracket_logs_that_asbb_mw_has_no_branch_for():
    profile, warnings = reg.detect_custom_parser(ILA_BANK_SAMPLE)
    assert profile is not None
    assert profile.name == ILA_BANK_NAME
    # ASBB MW's detect() matches the header shape too, so the ambiguity is
    # real and must be surfaced rather than silently resolved.
    assert len(warnings) == 1
    assert "Multiple custom parsers matched" in warnings[0]


def test_ila_bank_defers_when_the_sample_carries_asbb_mw_case_markers():
    # ASBB_MW_CREDIT_SAMPLE has both an "Inputs (...)" call and "Warrning" --
    # ASBB MW decodes those into structure, so the ILA Bank parser must not
    # claim the file and drag it into a field-yield tie-break.
    from backend.custom_parsers import parser_ILA_Bank
    assert parser_ILA_Bank.detect(ASBB_MW_CREDIT_SAMPLE) is False
    assert parser_ILA_Bank.detect(ILA_BANK_SAMPLE) is True
    # Nothing resembling a timestamp+[LEVEL] header at all.
    assert parser_ILA_Bank.detect(ABCE_DEBIT_SAMPLE) is False


def test_ila_bank_run_preserves_stack_trace_json_and_abbreviated_levels(tmp_path):
    profile = reg.get_custom_profile_by_name(ILA_BANK_NAME)
    log_path = tmp_path / "ila_bank.log"
    log_path.write_text(ILA_BANK_SAMPLE)

    from datetime import datetime, timezone
    events = reg.run_custom_parser(
        profile=profile,
        file_path=str(log_path),
        batch_id="batch-3",
        file_name="ila_bank.log",
        upload_time=datetime.now(timezone.utc),
    )

    assert len(events) == 4

    # [INF]/[ERR] are Serilog spellings the shared normalize_level() maps to
    # UNKNOWN; the adapter's alias table is what makes them real severities.
    assert events[0].level == "INFO"
    assert events[1].level == "ERROR"

    first = events[0].attributes["details"]
    assert events[0].attributes["correlation_id"] == "TRK-9001"
    assert first["transaction_ids"]["TranReference"] == ["778451"]
    assert first["ip_addresses"][0] == {"ip": "10.20.30.40", "port": 8443, "original": "10.20.30.40:8443"}

    # The continuation line belongs to the ERROR entry, not a record of its own.
    err = events[1].attributes["details"]
    assert err["exceptions"] == ["System.Net.Http.HttpRequestException"]
    assert err["status_codes"][0]["code"] == 500
    assert err["stack_frames"][0]["line"] == "142"
    assert "at Afs.Core.Adapter.PostAsync" in events[1].raw

    assert events[2].attributes["details"]["embedded_json"] == [{"amount": 500.0, "currency": "USD"}]

    # Untracked line: no tracker to correlate on, but the duration is kept.
    assert events[3].attributes["correlation_id"] is None
    assert events[3].attributes["details"]["durations"][0]["milliseconds"] == 1549


def test_ila_bank_keeps_original_timestamp_text_alongside_the_normalized_one():
    """The whole point of this parser: normalization is additive, never a
    replacement for what the file actually said."""
    from backend.custom_parsers import parser_ILA_Bank
    import tempfile, os

    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as tf:
        tf.write(ILA_BANK_SAMPLE)
        path = tf.name
    try:
        records = parser_ILA_Bank.parse_log_file(path)
    finally:
        os.unlink(path)

    assert records[0]["timestamp"] == "2026-08-27 09:10:02.451 +00:00"
    assert records[0]["details"]["timestamp_normalized"] == "2026-08-27T09:10:02.451000+00:00"
    assert records[0]["details"]["timestamp_precision_digits"] == 3
    # Concatenating every record's raw block reproduces the source exactly.
    assert "".join(r["_raw_block"] for r in records) == ILA_BANK_SAMPLE
