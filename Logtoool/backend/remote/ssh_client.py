"""
SSH/SFTP client for pulling logs from remote machines.

Host key verification uses (TOFU): the first successful
connection to a machine records the host key's fingerprint; every
connection after that must present the same fingerprint or the connection
is refused. This is the same trust model SSH clients use by default
(~/.ssh/known_hosts) -- it doesn't require pre-distributing host keys, but
it does mean a MITM on the very first connection would go undetected. For
an institutional deployment, seeding `host_key_fingerprint` out-of-band
(e.g. from a value provided by whoever administers the target machine)
before the first connection closes that gap; that's a config choice left
to the admin, not something this client can enforce on its own.
"""

import hashlib
import io
import stat
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import paramiko


class SSHConnectionError(Exception):
    pass


class HostKeyMismatchError(SSHConnectionError):
    """Raised when a machine's presented host key doesn't match the
    fingerprint recorded on a previous successful connection -- this is
    exactly the scenario TOFU exists to catch (key rotated legitimately, or
    something is impersonating the host) and must never be silently
    ignored or auto-accepted."""

    pass


@dataclass
class RemoteFileInfo:
    path: str
    mtime: float
    size: int


class _RecordingHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Accepts any host key on first use and records its fingerprint,
    rather than paramiko's default (silently trust) or reject-everything
    behavior -- we want TOFU, not either extreme."""

    def __init__(self):
        self.fingerprint: Optional[str] = None

    def missing_host_key(self, client, hostname, key):
        self.fingerprint = hashlib.sha256(key.asbytes()).hexdigest()


def _connect(
    host: str,
    port: int,
    username: str,
    auth_type: str,
    secret: str,
    expected_fingerprint: Optional[str],
    timeout: int = 15,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    policy = _RecordingHostKeyPolicy()
    client.set_missing_host_key_policy(policy)

    try:
        if auth_type == "password":
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=secret,
                timeout=timeout,
            )
        elif auth_type == "key":
            key_file = io.StringIO(secret)
            pkey = None
            for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
                try:
                    key_file.seek(0)
                    pkey = key_cls.from_private_key(key_file)
                    break
                except paramiko.SSHException:
                    continue
            if pkey is None:
                raise SSHConnectionError(
                    "Private key could not be parsed (tried Ed25519/RSA/ECDSA formats)"
                )
            client.connect(
                hostname=host, port=port, username=username, pkey=pkey, timeout=timeout
            )
        else:
            raise SSHConnectionError(f"Unknown auth_type: {auth_type!r}")
    except paramiko.AuthenticationException as e:
        raise SSHConnectionError(f"Authentication failed: {e}")
    except (paramiko.SSHException, OSError) as e:
        raise SSHConnectionError(f"Connection failed: {e}")

    presented_fingerprint = policy.fingerprint
    if expected_fingerprint and presented_fingerprint != expected_fingerprint:
        client.close()
        raise HostKeyMismatchError(
            f"Host key for {host}:{port} does not match the fingerprint recorded on a "
            f"previous connection (expected {expected_fingerprint[:16]}..., got "
            f"{presented_fingerprint[:16] if presented_fingerprint else 'none'}...). "
            f"Refusing to connect -- this could mean the host's key was legitimately "
            f"rotated, or something else is answering on this address. Verify out-of-band "
            f"before updating the stored fingerprint."
        )

    client._tofu_fingerprint = (
        presented_fingerprint  # stashed for the caller to persist on first connect
    )
    return client


def test_connection(
    host: str,
    port: int,
    username: str,
    auth_type: str,
    secret: str,
    expected_fingerprint: Optional[str],
) -> str:
    """Connects and returns the host key fingerprint on success (the caller
    persists it if this was the first connection). Raises SSHConnectionError
    / HostKeyMismatchError on failure."""
    client = _connect(host, port, username, auth_type, secret, expected_fingerprint)
    try:
        client.get_transport().send_ignore()  # cheap liveness check
    finally:
        fingerprint = client._tofu_fingerprint
        client.close()
    return fingerprint


def list_remote_log_files(
    host: str,
    port: int,
    username: str,
    auth_type: str,
    secret: str,
    expected_fingerprint: Optional[str],
    remote_directory: str,
    recursive: bool,
    allowed_extensions: Set[str],
) -> Tuple[List[RemoteFileInfo], str]:
    """Returns (files, host_key_fingerprint)."""
    client = _connect(host, port, username, auth_type, secret, expected_fingerprint)
    try:
        sftp = client.open_sftp()
        files: List[RemoteFileInfo] = []
        _walk_sftp(sftp, remote_directory, recursive, allowed_extensions, files)
        fingerprint = client._tofu_fingerprint
        return files, fingerprint
    except FileNotFoundError:
        raise SSHConnectionError(f"Remote directory not found: {remote_directory}")
    except PermissionError:
        raise SSHConnectionError(f"Permission denied reading: {remote_directory}")
    finally:
        client.close()


def _walk_sftp(
    sftp,
    path: str,
    recursive: bool,
    allowed_extensions: Set[str],
    out: List[RemoteFileInfo],
) -> None:
    for entry in sftp.listdir_attr(path):
        entry_path = f"{path.rstrip('/')}/{entry.filename}"
        if stat.S_ISDIR(entry.st_mode):
            if recursive:
                _walk_sftp(sftp, entry_path, recursive, allowed_extensions, out)
            continue
        ext = (
            "." + entry.filename.rsplit(".", 1)[-1].lower()
            if "." in entry.filename
            else ""
        )
        if ext in allowed_extensions:
            out.append(
                RemoteFileInfo(
                    path=entry_path,
                    mtime=float(entry.st_mtime),
                    size=int(entry.st_size),
                )
            )


def fetch_remote_file(
    host: str,
    port: int,
    username: str,
    auth_type: str,
    secret: str,
    expected_fingerprint: Optional[str],
    remote_path: str,
    start_offset: int = 0,
) -> bytes:
    """Fetches remote_path's contents starting at start_offset -- used for
    incremental reads of a growing append-only log file. start_offset=0
    fetches the whole file (first-ever pull, or a file that was rotated)."""
    client = _connect(host, port, username, auth_type, secret, expected_fingerprint)
    try:
        sftp = client.open_sftp()
        with sftp.open(remote_path, "rb") as f:
            if start_offset:
                f.seek(start_offset)
            return f.read()
    finally:
        client.close()
