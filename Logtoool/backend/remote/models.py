"""
Remote machine registry. Shares the same declarative Base as core/store.py
and auth/models.py -- one SQLite file, one Base.metadata.create_all() call
picks up every model regardless of which module defines it.
"""

from sqlalchemy import Column, Float, Integer, String, Text

from backend.core.store import Base


class RemoteMachineModel(Base):
    __tablename__ = "remote_machines"

    machine_id = Column(String, primary_key=True)
    label = Column(String, nullable=False)  # human-readable name, e.g. "prod-db-01"
    host = Column(String, nullable=False)
    port = Column(Integer, nullable=False, default=22)
    username = Column(String, nullable=False)

    auth_type = Column(String, nullable=False)  # 'password' or 'key'
    encrypted_secret = Column(
        Text, nullable=False
    )  # encrypted password OR encrypted private key

    remote_directory = Column(String, nullable=False)
    recursive = Column(Integer, nullable=False, default=1)

    poll_interval_minutes = Column(Integer, nullable=False, default=15)
    enabled = Column(Integer, nullable=False, default=1)

    # TOFU (trust on first use) host key pinning -- populated on first
    # successful connection, verified on every connection after that.
    host_key_fingerprint = Column(String, nullable=True)

    created_at = Column(String, nullable=False)
    created_by_user_id = Column(String, nullable=True)

    last_polled_at = Column(String, nullable=True)
    last_status = Column(
        String, nullable=True
    )  # 'success' | 'error' | None (never polled)
    last_error = Column(Text, nullable=True)
    last_files_ingested = Column(Integer, nullable=True)


class RemoteFileCheckpointModel(Base):
    """Tracks per-file ingestion progress so repeated polls only fetch new
    content from a growing (append-only) log file, rather than re-ingesting
    the whole file every cycle -- the latter would duplicate every event on
    every poll, which is worse than not polling at all. If a file shrinks
    (rotated/truncated/replaced) the byte offset resets to 0, since the
    previously-tracked content is no longer at that offset."""

    __tablename__ = "remote_file_checkpoints"

    checkpoint_id = Column(String, primary_key=True)
    machine_id = Column(String, nullable=False)
    remote_path = Column(String, nullable=False)
    remote_mtime = Column(Float, nullable=False)
    remote_size = Column(Integer, nullable=False)
    bytes_ingested = Column(Integer, nullable=False, default=0)
    last_ingested_at = Column(String, nullable=False)
    last_batch_id = Column(String, nullable=True)
