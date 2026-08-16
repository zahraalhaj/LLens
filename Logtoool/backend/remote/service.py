"""
RemoteMachineService owns the machine registry, connection testing, and
polling. Polling reuses the existing LogIngestionEngine/DatabaseManager/
AlertRulesProcessor exactly as the browser-upload and directory-ingestion
routes do -- a file pulled over SFTP goes through the identical parsing,
profile-detection, and alerting pipeline as any other ingestion path, not
a separate one.
"""
import io
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.alerts.rules import AlertRulesProcessor
from backend.core.ingest import LogIngestionEngine
from backend.core.store import Base, DatabaseManager
from backend.remote import crypto, ssh_client
from backend.remote.models import RemoteFileCheckpointModel, RemoteMachineModel

logger = logging.getLogger("logtool.remote")

ALLOWED_REMOTE_EXTENSIONS = {".log", ".txt", ".jsonl", ".csv", ".tsv"}
VALID_AUTH_TYPES = {"password", "key"}


class MachineNotFoundError(Exception):
    pass


@dataclass
class PollResult:
    machine_id: str
    files_found: int
    files_ingested: int
    files_unchanged: int
    files_rotated: int
    total_events_ingested: int
    errors: List[str]


class RemoteMachineService:
    def __init__(
        self,
        db_path: str,
        ingestion_engine: LogIngestionEngine,
        db_manager: DatabaseManager,
        alert_processor: AlertRulesProcessor,
    ):
        self.ingestion_engine = ingestion_engine
        self.db_manager = db_manager
        self.alert_processor = alert_processor
        self.engine = create_engine(
            f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30.0}, echo=False
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    # -- CRUD -------------------------------------------------------------

    def create_machine(
        self,
        label: str,
        host: str,
        port: int,
        username: str,
        auth_type: str,
        secret: str,
        remote_directory: str,
        recursive: bool,
        poll_interval_minutes: int,
        created_by_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if auth_type not in VALID_AUTH_TYPES:
            raise ValueError(f"auth_type must be one of {VALID_AUTH_TYPES}")

        session = self.Session()
        try:
            machine = RemoteMachineModel(
                machine_id=str(uuid.uuid4()),
                label=label,
                host=host,
                port=port,
                username=username,
                auth_type=auth_type,
                encrypted_secret=crypto.encrypt_secret(secret),
                remote_directory=remote_directory,
                recursive=1 if recursive else 0,
                poll_interval_minutes=poll_interval_minutes,
                enabled=1,
                created_at=datetime.now(timezone.utc).isoformat(),
                created_by_user_id=created_by_user_id,
            )
            session.add(machine)
            session.commit()
            logger.info(f"Registered remote machine '{label}' ({host}:{port})")
            return self._to_public_dict(machine)
        finally:
            session.close()

    def list_machines(self) -> List[Dict[str, Any]]:
        session = self.Session()
        try:
            machines = session.query(RemoteMachineModel).order_by(RemoteMachineModel.created_at).all()
            return [self._to_public_dict(m) for m in machines]
        finally:
            session.close()

    def get_machine(self, machine_id: str) -> Dict[str, Any]:
        session = self.Session()
        try:
            machine = session.query(RemoteMachineModel).filter_by(machine_id=machine_id).first()
            if not machine:
                raise MachineNotFoundError(machine_id)
            return self._to_public_dict(machine)
        finally:
            session.close()

    def update_machine(self, machine_id: str, **fields) -> Dict[str, Any]:
        session = self.Session()
        try:
            machine = session.query(RemoteMachineModel).filter_by(machine_id=machine_id).first()
            if not machine:
                raise MachineNotFoundError(machine_id)

            if "secret" in fields:
                secret = fields.pop("secret")
                if secret:  # empty string means "leave unchanged" (edit form doesn't re-show secrets)
                    machine.encrypted_secret = crypto.encrypt_secret(secret)
                    machine.host_key_fingerprint = None  # credentials changed -- re-verify host identity fresh

            for key in ("label", "host", "port", "username", "auth_type", "remote_directory", "poll_interval_minutes"):
                if key in fields and fields[key] is not None:
                    setattr(machine, key, fields[key])
            if "recursive" in fields and fields["recursive"] is not None:
                machine.recursive = 1 if fields["recursive"] else 0
            if "enabled" in fields and fields["enabled"] is not None:
                machine.enabled = 1 if fields["enabled"] else 0

            session.commit()
            return self._to_public_dict(machine)
        finally:
            session.close()

    def delete_machine(self, machine_id: str) -> None:
        session = self.Session()
        try:
            session.query(RemoteMachineModel).filter_by(machine_id=machine_id).delete()
            session.query(RemoteFileCheckpointModel).filter_by(machine_id=machine_id).delete()
            session.commit()
        finally:
            session.close()

    def _to_public_dict(self, m: RemoteMachineModel) -> Dict[str, Any]:
        # encrypted_secret is deliberately never included -- not even encrypted
        # form, since there's no legitimate reason for the API response to
        # carry it and every reason not to.
        return {
            "machine_id": m.machine_id,
            "label": m.label,
            "host": m.host,
            "port": m.port,
            "username": m.username,
            "auth_type": m.auth_type,
            "remote_directory": m.remote_directory,
            "recursive": bool(m.recursive),
            "poll_interval_minutes": m.poll_interval_minutes,
            "enabled": bool(m.enabled),
            "host_key_fingerprint": m.host_key_fingerprint,
            "created_at": m.created_at,
            "last_polled_at": m.last_polled_at,
            "last_status": m.last_status,
            "last_error": m.last_error,
            "last_files_ingested": m.last_files_ingested,
        }

    # -- Connection testing -------------------------------------------------

    def test_connection(self, machine_id: str) -> Dict[str, Any]:
        session = self.Session()
        try:
            machine = session.query(RemoteMachineModel).filter_by(machine_id=machine_id).first()
            if not machine:
                raise MachineNotFoundError(machine_id)
            secret = crypto.decrypt_secret(machine.encrypted_secret)
            try:
                fingerprint = ssh_client.test_connection(
                    machine.host, machine.port, machine.username, machine.auth_type, secret,
                    machine.host_key_fingerprint,
                )
            except ssh_client.SSHConnectionError as e:
                return {"success": False, "message": str(e)}

            first_connection = machine.host_key_fingerprint is None
            if first_connection:
                machine.host_key_fingerprint = fingerprint
                session.commit()

            return {
                "success": True,
                "message": "Connected successfully."
                + (" Host key recorded for future verification." if first_connection else ""),
                "host_key_fingerprint": fingerprint,
            }
        finally:
            session.close()

    # -- Polling ----------------------------------------------------------

    def poll_machine(self, machine_id: str) -> PollResult:
        # Deliberately short-lived sessions throughout this method rather
        # than one held open for its whole duration: ingestion_engine and
        # db_manager each use their own separate SQLAlchemy engine against
        # the same SQLite file, and holding our own session/transaction
        # open while those nested calls try to write causes "database is
        # locked" contention. Fetch what we need, close the session, do the
        # slow/network work, then open a fresh session to persist results.
        session = self.Session()
        try:
            machine = session.query(RemoteMachineModel).filter_by(machine_id=machine_id).first()
            if not machine:
                raise MachineNotFoundError(machine_id)
            host, port, username, auth_type = machine.host, machine.port, machine.username, machine.auth_type
            encrypted_secret = machine.encrypted_secret
            host_key_fingerprint = machine.host_key_fingerprint
            remote_directory = machine.remote_directory
            recursive = bool(machine.recursive)
            label = machine.label
        finally:
            session.close()

        secret = crypto.decrypt_secret(encrypted_secret)
        errors: List[str] = []
        files_ingested = 0
        files_unchanged = 0
        files_rotated = 0
        total_events = 0

        try:
            remote_files, fingerprint = ssh_client.list_remote_log_files(
                host, port, username, auth_type, secret,
                host_key_fingerprint, remote_directory, recursive,
                ALLOWED_REMOTE_EXTENSIONS,
            )
        except ssh_client.SSHConnectionError as e:
            self._record_poll_outcome(machine_id, status="error", error=str(e), files_ingested=0)
            logger.error(f"Poll failed for machine '{label}': {e}")
            return PollResult(machine_id, 0, 0, 0, 0, 0, [str(e)])

        if host_key_fingerprint is None and fingerprint:
            host_key_fingerprint = fingerprint
            self._update_host_key_fingerprint(machine_id, fingerprint)

        for remote_file in remote_files:
            checkpoint = self._get_checkpoint(machine_id, remote_file.path)

            if checkpoint and checkpoint["remote_size"] == remote_file.size and checkpoint["remote_mtime"] == remote_file.mtime:
                files_unchanged += 1
                continue

            rotated = checkpoint is not None and remote_file.size < checkpoint["bytes_ingested"]
            start_offset = 0 if (checkpoint is None or rotated) else checkpoint["bytes_ingested"]
            if rotated:
                files_rotated += 1

            try:
                content = ssh_client.fetch_remote_file(
                    host, port, username, auth_type, secret, host_key_fingerprint, remote_file.path, start_offset,
                )
            except ssh_client.SSHConnectionError as e:
                errors.append(f"{remote_file.path}: {e}")
                continue

            if not content:
                continue

            try:
                summary = self.ingestion_engine.ingest_file_stream(
                    file_obj=io.BytesIO(content),
                    file_name=f"{label}:{remote_file.path}",
                    file_size_bytes=len(content),
                )
                total_events += summary.parsed_events_count
                files_ingested += 1

                if summary.parsed_events_count > 0:
                    events, _ = self.db_manager.query_events(
                        page=1, page_size=summary.parsed_events_count, batch_id=summary.batch_id
                    )
                    try:
                        self.alert_processor.evaluate_batch_alerts(batch_id=summary.batch_id, events=events)
                    except Exception:
                        logger.exception(f"Alert evaluation failed for remote batch {summary.batch_id}")
            except Exception as e:
                logger.exception(f"Ingestion failed for {remote_file.path} from '{label}'")
                errors.append(f"{remote_file.path}: {e}")
                continue

            new_bytes_ingested = start_offset + len(content)
            self._upsert_checkpoint(
                machine_id, remote_file.path, remote_file.mtime, remote_file.size, new_bytes_ingested, summary.batch_id
            )

        self._record_poll_outcome(
            machine_id,
            status="success" if not errors else "error",
            error="; ".join(errors) if errors else None,
            files_ingested=files_ingested,
        )

        logger.info(
            f"Polled '{label}': {len(remote_files)} file(s) found, {files_ingested} ingested, "
            f"{files_unchanged} unchanged, {total_events} event(s), {len(errors)} error(s)"
        )

        return PollResult(machine_id, len(remote_files), files_ingested, files_unchanged, files_rotated, total_events, errors)

    def _get_checkpoint(self, machine_id: str, remote_path: str) -> Optional[Dict[str, Any]]:
        session = self.Session()
        try:
            cp = (
                session.query(RemoteFileCheckpointModel)
                .filter_by(machine_id=machine_id, remote_path=remote_path)
                .first()
            )
            if not cp:
                return None
            return {"remote_size": cp.remote_size, "remote_mtime": cp.remote_mtime, "bytes_ingested": cp.bytes_ingested}
        finally:
            session.close()

    def _upsert_checkpoint(
        self, machine_id: str, remote_path: str, mtime: float, size: int, bytes_ingested: int, batch_id: str
    ) -> None:
        session = self.Session()
        try:
            cp = (
                session.query(RemoteFileCheckpointModel)
                .filter_by(machine_id=machine_id, remote_path=remote_path)
                .first()
            )
            now = datetime.now(timezone.utc).isoformat()
            if cp:
                cp.remote_mtime = mtime
                cp.remote_size = size
                cp.bytes_ingested = bytes_ingested
                cp.last_ingested_at = now
                cp.last_batch_id = batch_id
            else:
                session.add(
                    RemoteFileCheckpointModel(
                        checkpoint_id=str(uuid.uuid4()),
                        machine_id=machine_id,
                        remote_path=remote_path,
                        remote_mtime=mtime,
                        remote_size=size,
                        bytes_ingested=bytes_ingested,
                        last_ingested_at=now,
                        last_batch_id=batch_id,
                    )
                )
            session.commit()
        finally:
            session.close()

    def _update_host_key_fingerprint(self, machine_id: str, fingerprint: str) -> None:
        session = self.Session()
        try:
            machine = session.query(RemoteMachineModel).filter_by(machine_id=machine_id).first()
            if machine and machine.host_key_fingerprint is None:
                machine.host_key_fingerprint = fingerprint
                session.commit()
        finally:
            session.close()

    def _record_poll_outcome(self, machine_id: str, status: str, error: Optional[str], files_ingested: int) -> None:
        session = self.Session()
        try:
            machine = session.query(RemoteMachineModel).filter_by(machine_id=machine_id).first()
            if machine:
                machine.last_polled_at = datetime.now(timezone.utc).isoformat()
                machine.last_status = status
                machine.last_error = error
                machine.last_files_ingested = files_ingested
                session.commit()
        finally:
            session.close()

    def enabled_machines_due_for_poll(self) -> List[str]:
        """Used by the scheduler -- returns machine_ids that are enabled and
        either never polled or past their poll interval."""
        session = self.Session()
        try:
            machines = session.query(RemoteMachineModel).filter_by(enabled=1).all()
            due = []
            now = datetime.now(timezone.utc)
            for m in machines:
                if not m.last_polled_at:
                    due.append(m.machine_id)
                    continue
                last = datetime.fromisoformat(m.last_polled_at)
                elapsed_minutes = (now - last).total_seconds() / 60
                if elapsed_minutes >= m.poll_interval_minutes:
                    due.append(m.machine_id)
            return due
        finally:
            session.close()
