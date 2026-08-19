from datetime import datetime, timezone

import pytest

from backend.alerts.email import EmailDispatcher
from backend.analysis.vplus_alerting import VPlusAvailabilityMonitor
from backend.core.ingest import LogIngestionEngine
from backend.core.profiles import ProfileManager
from backend.core.store import DatabaseManager

SOURCE_SYSTEM = "afs_netcetera_3ds_stepup"


@pytest.fixture
def setup(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path=db_path)
    pm = ProfileManager(profiles_dir=str(tmp_path / "profiles"))
    engine = LogIngestionEngine(db_manager=db, profile_manager=pm)
    monitor = VPlusAvailabilityMonitor(
        db_path=db_path, db_manager=db, email_dispatcher=EmailDispatcher(),
        gap_threshold_minutes=10, lookback_hours=24,
    )
    return db, engine, monitor, db_path


def _ingest_line(engine, dt, tracker="SU12345", message="Stepup Responce to Netcetra: {}"):
    import io
    line = f'{dt.strftime("%m/%d/%Y %I:%M:%S %p")} Log Tracker No: {tracker} => {message}\n'
    engine.ingest_file_stream(file_obj=io.BytesIO(line.encode()), file_name="t.log", file_size_bytes=len(line))


def test_no_data_at_all_is_treated_as_down(setup):
    db, engine, monitor, _ = setup
    result = monitor.check_and_alert()
    assert result["now_down"] is True
    assert result["transitioned"] is True  # first check, no prior state -> alerts


def test_second_check_with_still_no_data_does_not_re_alert(setup):
    db, engine, monitor, _ = setup
    first = monitor.check_and_alert()
    second = monitor.check_and_alert()
    assert first["transitioned"] is True
    assert second["transitioned"] is False  # already known down, no duplicate alert


def test_recent_activity_reports_healthy(setup):
    db, engine, monitor, _ = setup
    now = datetime.now(timezone.utc)
    _ingest_line(engine, now)
    result = monitor.check_and_alert()
    assert result["now_down"] is False


def test_transition_from_down_to_up_is_detected(setup):
    db, engine, monitor, _ = setup

    # First check: no data -> down, alerted.
    first = monitor.check_and_alert()
    assert first["now_down"] is True

    # Now ingest a fresh event and check again -- should transition to healthy.
    now = datetime.now(timezone.utc)
    _ingest_line(engine, now)
    second = monitor.check_and_alert()
    assert second["now_down"] is False
    assert second["transitioned"] is True  # down -> up transition, should alert "recovered"


def test_state_persists_across_new_monitor_instance(setup):
    db, engine, monitor, db_path = setup
    monitor.check_and_alert()  # establishes "down" state

    # Fresh monitor instance, same DB file -- simulates a process restart.
    fresh_monitor = VPlusAvailabilityMonitor(
        db_path=db_path, db_manager=db, email_dispatcher=EmailDispatcher(),
        gap_threshold_minutes=10, lookback_hours=24,
    )
    result = fresh_monitor.check_and_alert()
    assert result["transitioned"] is False  # still known-down from before, no duplicate alert
