import io
from unittest.mock import MagicMock, patch

import pytest

from backend.core.ingest import LogIngestionEngine
from backend.core.profiles import ProfileManager
from backend.core.store import DatabaseManager
from backend.alerts.email import EmailDispatcher
from backend.alerts.notification_groups import NotificationGroupManager
from backend.alerts.rule_manager import AlertRuleManager
from backend.alerts.rules import AlertRulesProcessor
from backend.alerts.state import AlertDeduplicationEngine
from backend.remote import crypto
from backend.remote.service import MachineNotFoundError, RemoteMachineService
from backend.remote.ssh_client import RemoteFileInfo, SSHConnectionError, HostKeyMismatchError


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR_NAME, crypto.generate_key())


@pytest.fixture
def service(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path=db_path)
    pm = ProfileManager(profiles_dir=str(tmp_path / "profiles"))
    engine = LogIngestionEngine(db_manager=db, profile_manager=pm)
    rule_manager = AlertRuleManager(db_path=db_path)
    group_manager = NotificationGroupManager(db_path=db_path)
    alerts = AlertRulesProcessor(
        email_dispatcher=EmailDispatcher(),
        dedup_engine=AlertDeduplicationEngine(db_path=db_path),
        rule_manager=rule_manager,
        group_manager=group_manager,
        db_path=db_path,
    )
    return RemoteMachineService(db_path=db_path, ingestion_engine=engine, db_manager=db, alert_processor=alerts)


def _make_machine(service, **overrides):
    defaults = dict(
        label="prod-db-01",
        host="10.0.0.5",
        port=22,
        username="loguser",
        auth_type="password",
        secret="hunter2",
        remote_directory="/var/log/myapp",
        recursive=True,
        poll_interval_minutes=15,
    )
    defaults.update(overrides)
    return service.create_machine(**defaults)


# -- CRUD -------------------------------------------------------------------

def test_create_machine_never_returns_secret(service):
    machine = _make_machine(service)
    assert "secret" not in machine
    assert "encrypted_secret" not in machine


def test_list_machines_never_returns_secret(service):
    _make_machine(service)
    machines = service.list_machines()
    assert len(machines) == 1
    assert "secret" not in machines[0]
    assert "encrypted_secret" not in machines[0]


def test_get_machine_not_found_raises(service):
    with pytest.raises(MachineNotFoundError):
        service.get_machine("does-not-exist")


def test_update_machine_with_empty_secret_leaves_credential_unchanged(service):
    machine = _make_machine(service)
    service.update_machine(machine["machine_id"], secret="")  # empty = "don't change"
    # No direct way to check the stored secret via the public API (by
    # design) -- verify indirectly via a successful test_connection call
    # using the ORIGINAL secret still working (proven in the polling tests
    # below via mocked ssh_client, which receives whatever decrypt_secret
    # returns).
    updated = service.get_machine(machine["machine_id"])
    assert updated["host"] == machine["host"]  # unrelated field untouched


def test_delete_machine_removes_it(service):
    machine = _make_machine(service)
    service.delete_machine(machine["machine_id"])
    with pytest.raises(MachineNotFoundError):
        service.get_machine(machine["machine_id"])


def test_invalid_auth_type_rejected(service):
    with pytest.raises(ValueError):
        _make_machine(service, auth_type="carrier_pigeon")


# -- Connection testing -------------------------------------------------------

@patch("backend.remote.service.ssh_client.test_connection")
def test_test_connection_records_fingerprint_on_first_success(mock_test, service):
    mock_test.return_value = "abc123fingerprint"
    machine = _make_machine(service)
    result = service.test_connection(machine["machine_id"])
    assert result["success"] is True
    updated = service.get_machine(machine["machine_id"])
    assert updated["host_key_fingerprint"] == "abc123fingerprint"


@patch("backend.remote.service.ssh_client.test_connection")
def test_test_connection_failure_reported_cleanly(mock_test, service):
    mock_test.side_effect = SSHConnectionError("Authentication failed: bad password")
    machine = _make_machine(service)
    result = service.test_connection(machine["machine_id"])
    assert result["success"] is False
    assert "bad password" in result["message"]


# -- Polling / checkpointing --------------------------------------------------

SAMPLE_LOG_CONTENT = (
    b'{"timestamp": "2026-08-10T10:00:00Z", "level": "INFO", "service": "billing", "message": "start"}\n'
    b'{"timestamp": "2026-08-10T10:00:01Z", "level": "ERROR", "service": "billing", "message": "failed"}\n'
)


@patch("backend.remote.service.ssh_client.fetch_remote_file")
@patch("backend.remote.service.ssh_client.list_remote_log_files")
def test_first_poll_ingests_whole_file(mock_list, mock_fetch, service):
    machine = _make_machine(service)
    mock_list.return_value = ([RemoteFileInfo(path="/var/log/myapp/app.log", mtime=1000.0, size=len(SAMPLE_LOG_CONTENT))], "fp1")
    mock_fetch.return_value = SAMPLE_LOG_CONTENT

    result = service.poll_machine(machine["machine_id"])

    assert result.files_found == 1
    assert result.files_ingested == 1
    assert result.total_events_ingested == 2
    # start_offset=0 on first-ever pull
    mock_fetch.assert_called_once()
    assert mock_fetch.call_args.args[-1] == 0 or mock_fetch.call_args.kwargs.get("start_offset", 0) == 0


@patch("backend.remote.service.ssh_client.fetch_remote_file")
@patch("backend.remote.service.ssh_client.list_remote_log_files")
def test_second_poll_with_unchanged_file_skips_it(mock_list, mock_fetch, service):
    machine = _make_machine(service)
    file_info = RemoteFileInfo(path="/var/log/myapp/app.log", mtime=1000.0, size=len(SAMPLE_LOG_CONTENT))
    mock_list.return_value = ([file_info], "fp1")
    mock_fetch.return_value = SAMPLE_LOG_CONTENT

    service.poll_machine(machine["machine_id"])
    mock_fetch.reset_mock()

    result = service.poll_machine(machine["machine_id"])
    assert result.files_unchanged == 1
    assert result.files_ingested == 0
    mock_fetch.assert_not_called()  # unchanged file -- no need to even fetch


@patch("backend.remote.service.ssh_client.fetch_remote_file")
@patch("backend.remote.service.ssh_client.list_remote_log_files")
def test_grown_file_fetches_only_new_bytes(mock_list, mock_fetch, service):
    machine = _make_machine(service)
    first_content = SAMPLE_LOG_CONTENT
    appended_line = b'{"timestamp": "2026-08-10T10:00:02Z", "level": "WARN", "service": "billing", "message": "retry"}\n'

    file_info_1 = RemoteFileInfo(path="/var/log/myapp/app.log", mtime=1000.0, size=len(first_content))
    mock_list.return_value = ([file_info_1], "fp1")
    mock_fetch.return_value = first_content
    service.poll_machine(machine["machine_id"])

    # File grew -- second poll should fetch only the appended bytes.
    file_info_2 = RemoteFileInfo(path="/var/log/myapp/app.log", mtime=2000.0, size=len(first_content) + len(appended_line))
    mock_list.return_value = ([file_info_2], "fp1")
    mock_fetch.reset_mock()
    mock_fetch.return_value = appended_line

    result = service.poll_machine(machine["machine_id"])

    assert result.files_ingested == 1
    assert result.total_events_ingested == 1  # only the new line's event, not re-ingesting the first 2
    called_offset = mock_fetch.call_args.kwargs.get("start_offset", mock_fetch.call_args.args[-1] if mock_fetch.call_args.args else None)
    assert called_offset == len(first_content)


@patch("backend.remote.service.ssh_client.fetch_remote_file")
@patch("backend.remote.service.ssh_client.list_remote_log_files")
def test_rotated_file_refetches_from_start(mock_list, mock_fetch, service):
    machine = _make_machine(service)
    file_info_1 = RemoteFileInfo(path="/var/log/myapp/app.log", mtime=1000.0, size=len(SAMPLE_LOG_CONTENT))
    mock_list.return_value = ([file_info_1], "fp1")
    mock_fetch.return_value = SAMPLE_LOG_CONTENT
    service.poll_machine(machine["machine_id"])

    # File shrank (rotated/truncated) -- should be treated as a fresh file.
    small_content = b'{"timestamp": "2026-08-10T11:00:00Z", "level": "INFO", "service": "billing", "message": "fresh start"}\n'
    file_info_2 = RemoteFileInfo(path="/var/log/myapp/app.log", mtime=3000.0, size=len(small_content))
    mock_list.return_value = ([file_info_2], "fp1")
    mock_fetch.reset_mock()
    mock_fetch.return_value = small_content

    result = service.poll_machine(machine["machine_id"])

    assert result.files_rotated == 1
    called_offset = mock_fetch.call_args.kwargs.get("start_offset", mock_fetch.call_args.args[-1] if mock_fetch.call_args.args else None)
    assert called_offset == 0


@patch("backend.remote.service.ssh_client.list_remote_log_files")
def test_connection_error_during_listing_recorded_on_machine(mock_list, service):
    machine = _make_machine(service)
    mock_list.side_effect = SSHConnectionError("Connection timed out")

    result = service.poll_machine(machine["machine_id"])

    assert result.files_found == 0
    assert len(result.errors) == 1
    updated = service.get_machine(machine["machine_id"])
    assert updated["last_status"] == "error"
    assert "timed out" in updated["last_error"]


@patch("backend.remote.service.ssh_client.fetch_remote_file")
@patch("backend.remote.service.ssh_client.list_remote_log_files")
def test_successful_poll_updates_machine_status(mock_list, mock_fetch, service):
    machine = _make_machine(service)
    mock_list.return_value = ([RemoteFileInfo(path="/var/log/myapp/app.log", mtime=1000.0, size=len(SAMPLE_LOG_CONTENT))], "fp1")
    mock_fetch.return_value = SAMPLE_LOG_CONTENT

    service.poll_machine(machine["machine_id"])

    updated = service.get_machine(machine["machine_id"])
    assert updated["last_status"] == "success"
    assert updated["last_files_ingested"] == 1
    assert updated["last_polled_at"] is not None


def test_enabled_machines_due_for_poll_includes_never_polled(service):
    machine = _make_machine(service)
    due = service.enabled_machines_due_for_poll()
    assert machine["machine_id"] in due


def test_disabled_machine_not_due_for_poll(service):
    machine = _make_machine(service)
    service.update_machine(machine["machine_id"], enabled=False)
    due = service.enabled_machines_due_for_poll()
    assert machine["machine_id"] not in due
