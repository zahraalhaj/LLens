"""
Basic login throttling: locks out a username after too many consecutive
failed attempts, for a cooldown window. Deliberately simple in-memory state
-- appropriate for a single-process app serving ~20 known users. If this
ever runs as multiple processes/replicas behind a load balancer, this
state would need to move to the shared SQLite DB (or Redis); noting that
now so it isn't a silent surprise later.
"""
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict


@dataclass
class _Attempts:
    count: int = 0
    locked_until: float = 0.0


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 900):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._state: Dict[str, _Attempts] = {}
        self._lock = Lock()

    def is_locked_out(self, username: str) -> bool:
        with self._lock:
            entry = self._state.get(username.lower())
            if not entry:
                return False
            return entry.locked_until > time.monotonic()

    def seconds_until_unlocked(self, username: str) -> int:
        with self._lock:
            entry = self._state.get(username.lower())
            if not entry:
                return 0
            remaining = entry.locked_until - time.monotonic()
            return max(0, int(remaining))

    def record_failure(self, username: str) -> None:
        key = username.lower()
        with self._lock:
            entry = self._state.setdefault(key, _Attempts())
            entry.count += 1
            if entry.count >= self.max_attempts:
                entry.locked_until = time.monotonic() + self.lockout_seconds

    def record_success(self, username: str) -> None:
        with self._lock:
            self._state.pop(username.lower(), None)
