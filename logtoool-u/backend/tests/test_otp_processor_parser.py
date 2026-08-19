import pytest

from backend.core import custom_parser_registry as reg

DISPLAY_NAME = "OTP Online Processor (SMS/Email XML)"

SAMPLE_LOG = (
    "8/10/2026 2:14:10 PM : Msg Received-- &lt;Msg&gt;&lt;Header&gt;&lt;mtrackingid&gt;IA11111&lt;/mtrackingid&gt;"
    "&lt;Org&gt;ABCBANK&lt;/Org&gt;&lt;Typ&gt;OTP&lt;/Typ&gt;&lt;Mobile&gt;96170000000&lt;/Mobile&gt;&lt;/Header&gt;"
    "&lt;Body&gt;&lt;OTP&gt;654321&lt;/OTP&gt;&lt;TranAmount&gt;250.00&lt;/TranAmount&gt;&lt;TranCurrency&gt;USD&lt;/TranCurrency&gt;"
    "&lt;MerchantName&gt;Book &amp; Bean&lt;/MerchantName&gt;&lt;/Body&gt;&lt;/Msg&gt;\n"
    "8/10/2026 2:14:11 PM : Message for Queue=mq-oab-otp-in-push\n"
    "8/10/2026 2:14:12 PM : SMS placed in queue MsgId:MID987\n"
    "8/10/2026 2:14:15 PM : SendEmail MQEmailSMG Message: &lt;EmailMsg&gt;&lt;Header&gt;&lt;Org&gt;ABCBANK&lt;/Org&gt;"
    "&lt;EmailTo&gt;user@example.com&lt;/EmailTo&gt;&lt;/Header&gt;&lt;Body&gt;&lt;EMAILBODY1&gt;&lt;MWTEXT&gt;"
    "&lt;OTP&gt;654321&lt;/OTP&gt;&lt;/MWTEXT&gt;&lt;/EMAILBODY1&gt;&lt;/Body&gt;&lt;/EmailMsg&gt;\n"
    "8/10/2026 2:14:20 PM : OTP Processed Successfully for Tracker IA11111\n"
    "8/10/2026 2:14:25 PM : Force Verify By Mobile for user 96170000000\n"
    "8/10/2026 2:14:30 PM : Some completely unrelated content with no known marker\n"
)


def test_otp_processor_detected():
    profile, warnings = reg.detect_custom_parser(SAMPLE_LOG)
    assert profile is not None
    assert profile.name == DISPLAY_NAME


def test_no_false_positive_on_unrelated_text():
    profile, _ = reg.detect_custom_parser("just some ordinary log text\nwith no special format\n")
    assert profile is None


def test_run_custom_parser_links_events_by_tracker(tmp_path):
    profile = reg.get_custom_profile_by_name(DISPLAY_NAME)
    log_path = tmp_path / "otp.log"
    log_path.write_text(SAMPLE_LOG)

    from datetime import datetime, timezone
    events = reg.run_custom_parser(
        profile=profile, file_path=str(log_path), batch_id="batch-1",
        file_name="otp.log", upload_time=datetime.now(timezone.utc),
    )

    # 6 recognized events + 1 unrecognized line, none discarded
    assert len(events) == 7

    msg_event = next(e for e in events if e.component == "msg_received_sms_xml")
    queue_event = next(e for e in events if e.component == "queue")
    email_event = next(e for e in events if e.component == "email_xml")

    # Every event for this OTP flow correlates to the same tracker number,
    # even the ones (queue/msg-id/email/otp-success/force-verify) that
    # don't carry their own tracker id in their raw text.
    assert msg_event.attributes["correlation_id"] == "IA11111"
    assert queue_event.attributes["correlation_id"] == "IA11111"
    assert email_event.attributes["correlation_id"] == "IA11111"

    # The XML payload was actually broken apart, not just echoed back.
    assert msg_event.attributes["details"]["parsed"]["otp"] == "654321"
    assert msg_event.attributes["details"]["parsed"]["merchant"] == "Book & Bean"

    # Later events see the fully-merged per-tracker record (aggregated
    # queue/email/otp_processed/force_verify state), not just their own
    # narrow payload.
    assert email_event.attributes["details"]["record"]["email"] == "user@example.com"
    assert email_event.attributes["details"]["record"]["otp_processed"] is True
    assert email_event.attributes["details"]["record"]["queue"] == "mq-oab-otp-in-push"


def test_unrecognized_line_surfaced_as_warn_not_discarded(tmp_path):
    profile = reg.get_custom_profile_by_name(DISPLAY_NAME)
    log_path = tmp_path / "otp.log"
    log_path.write_text(SAMPLE_LOG)

    from datetime import datetime, timezone
    events = reg.run_custom_parser(
        profile=profile, file_path=str(log_path), batch_id="batch-1",
        file_name="otp.log", upload_time=datetime.now(timezone.utc),
    )
    unrecognized = [e for e in events if e.component == "other"]
    assert len(unrecognized) == 1
    assert unrecognized[0].level == "WARN"
    assert unrecognized[0].attributes["correlation_id"] is None


def test_full_ingestion_pipeline_routes_to_otp_processor(tmp_path):
    import io
    from backend.core.ingest import LogIngestionEngine
    from backend.core.profiles import ProfileManager
    from backend.core.store import DatabaseManager

    db = DatabaseManager(db_path=str(tmp_path / "test.db"))
    pm = ProfileManager(profiles_dir=str(tmp_path / "profiles"))
    engine = LogIngestionEngine(db_manager=db, profile_manager=pm)

    file_obj = io.BytesIO(SAMPLE_LOG.encode("utf-8"))
    summary = engine.ingest_file_stream(file_obj=file_obj, file_name="otp.log", file_size_bytes=len(SAMPLE_LOG))

    assert summary.matched_profile == DISPLAY_NAME
    assert summary.parsed_events_count == 7

    events, total = db.query_events(page=1, page_size=10)
    assert total == 7
    assert all(e["source_system"] == "otp_online_processor" for e in events)
