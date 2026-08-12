"""
AuthService owns user accounts and sessions. Deliberately independent of
DatabaseManager (core/store.py) -- same underlying SQLite file, same shared
Base/metadata, but its own engine/session so this module has no dependency
on log-storage internals and can be tested in isolation.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.auth.models import SessionModel, UserModel
from backend.auth.security import (
    generate_session_token,
    hash_password,
    hash_token,
    verify_password,
)
from backend.core.store import Base

logger = logging.getLogger("logtool.auth")

DEFAULT_SESSION_LIFETIME_HOURS = 12


@dataclass
class AuthenticatedUser:
    """What the rest of the app gets back -- never exposes password_hash."""
    user_id: str
    username: str
    role: str
    must_change_password: bool = False


class UsernameTakenError(Exception):
    pass


class InvalidCurrentPasswordError(Exception):
    pass


class AuthService:
    def __init__(self, db_path: str = "data/logs.db", session_lifetime_hours: int = DEFAULT_SESSION_LIFETIME_HOURS):
        self.session_lifetime = timedelta(hours=session_lifetime_hours)
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30.0},
            echo=False,
        )
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys = ON;"))
            conn.commit()
        Base.metadata.create_all(self.engine)
        self._ensure_must_change_password_column()
        self.Session = sessionmaker(bind=self.engine)

    def _ensure_must_change_password_column(self) -> None:
        """Base.metadata.create_all() only creates missing TABLES, not
        missing COLUMNS on tables that already exist -- so a database
        created before this field existed needs a one-time ALTER TABLE.
        No migration framework in this project yet, so this is deliberately
        a minimal, idempotent, defensive check rather than a full system."""
        with self.engine.connect() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()]
            if cols and "must_change_password" not in cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1"))
                conn.commit()
                logger.info("Migrated users table: added must_change_password column.")

    # -- User management -----------------------------------------------

    def create_user(self, username: str, password: str, role: str = "member") -> AuthenticatedUser:
        if role not in ("admin", "member"):
            raise ValueError(f"invalid role: {role!r}")
        session = self.Session()
        try:
            existing = session.query(UserModel).filter_by(username=username).first()
            if existing:
                raise UsernameTakenError(f"username '{username}' is already taken")
            user = UserModel(
                user_id=str(uuid.uuid4()),
                username=username,
                password_hash=hash_password(password),
                role=role,
                created_at=datetime.now(timezone.utc).isoformat(),
                is_active=1,
                # Every account is admin-provisioned (no self-service signup),
                # so every new account starts with a password that must be
                # changed on first login.
                must_change_password=1,
            )
            session.add(user)
            session.commit()
            logger.info(f"Created user '{username}' (role={role})")
            return AuthenticatedUser(
                user_id=user.user_id, username=user.username, role=user.role, must_change_password=True
            )
        finally:
            session.close()

    def list_users(self) -> List[dict]:
        session = self.Session()
        try:
            users = session.query(UserModel).order_by(UserModel.created_at).all()
            return [
                {
                    "user_id": u.user_id,
                    "username": u.username,
                    "role": u.role,
                    "created_at": u.created_at,
                    "is_active": bool(u.is_active),
                    "must_change_password": bool(u.must_change_password),
                }
                for u in users
            ]
        finally:
            session.close()

    def set_user_active(self, user_id: str, is_active: bool) -> None:
        session = self.Session()
        try:
            user = session.query(UserModel).filter_by(user_id=user_id).first()
            if not user:
                raise ValueError("user not found")
            user.is_active = 1 if is_active else 0
            session.commit()
            if not is_active:
                # Deactivating a user should end their current session(s)
                # immediately, not whenever those sessions happen to expire.
                self.revoke_all_sessions_for_user(user_id)
        finally:
            session.close()

    def change_password(self, user_id: str, new_password: str) -> None:
        """Admin-initiated password reset -- sets a new (presumably
        temporary) password and marks it as must-change, matching the same
        expiry behavior as a freshly-created account."""
        session = self.Session()
        try:
            user = session.query(UserModel).filter_by(user_id=user_id).first()
            if not user:
                raise ValueError("user not found")
            user.password_hash = hash_password(new_password)
            user.must_change_password = 1
            session.commit()
            # Force re-login everywhere after a password change.
            self.revoke_all_sessions_for_user(user_id)
        finally:
            session.close()

    def force_password_reset(self, user_id: str) -> None:
        """Marks an existing user's current password as expired without
        changing it -- e.g. for a suspected-compromised account where you
        want them to pick a new password without an admin knowing the old
        or an interim one."""
        session = self.Session()
        try:
            user = session.query(UserModel).filter_by(user_id=user_id).first()
            if not user:
                raise ValueError("user not found")
            user.must_change_password = 1
            session.commit()
            self.revoke_all_sessions_for_user(user_id)
        finally:
            session.close()

    def self_change_password(self, user_id: str, current_password: str, new_password: str) -> str:
        """User-initiated password change (including the forced first-login
        flow). Verifies current_password, clears must_change_password, and
        issues a fresh session token -- all prior sessions (including the
        one used to call this) are revoked first, so a leaked initial
        password can't be replayed via an old session after the real user
        has changed it.

        Returns the new raw session token.
        """
        session = self.Session()
        try:
            user = session.query(UserModel).filter_by(user_id=user_id).first()
            if not user:
                raise ValueError("user not found")
            if not verify_password(current_password, user.password_hash):
                raise InvalidCurrentPasswordError("current password is incorrect")
            user.password_hash = hash_password(new_password)
            user.must_change_password = 0
            session.commit()
        finally:
            session.close()

        self.revoke_all_sessions_for_user(user_id)
        return self.create_session(user_id)

    def delete_user(self, user_id: str) -> None:
        session = self.Session()
        try:
            user = session.query(UserModel).filter_by(user_id=user_id).first()
            if user:
                session.delete(user)  # sessions cascade via ON DELETE CASCADE
                session.commit()
        finally:
            session.close()

    # -- Authentication & sessions ---------------------------------------

    def authenticate(self, username: str, password: str) -> Optional[AuthenticatedUser]:
        session = self.Session()
        try:
            user = session.query(UserModel).filter_by(username=username).first()
            if not user or not user.is_active:
                return None
            if not verify_password(password, user.password_hash):
                return None
            return AuthenticatedUser(
                user_id=user.user_id,
                username=user.username,
                role=user.role,
                must_change_password=bool(user.must_change_password),
            )
        finally:
            session.close()

    def create_session(self, user_id: str) -> str:
        """Returns the raw token to hand to the client as a cookie. Only its
        hash is persisted."""
        raw_token = generate_session_token()
        now = datetime.now(timezone.utc)
        session = self.Session()
        try:
            record = SessionModel(
                session_id=str(uuid.uuid4()),
                user_id=user_id,
                token_hash=hash_token(raw_token),
                created_at=now.isoformat(),
                expires_at=(now + self.session_lifetime).isoformat(),
            )
            session.add(record)
            session.commit()
            return raw_token
        finally:
            session.close()

    def validate_session(self, raw_token: str) -> Optional[AuthenticatedUser]:
        """Returns the authenticated user if the token maps to a live,
        non-expired session belonging to an active user; None otherwise.
        Expired sessions are opportunistically cleaned up here."""
        if not raw_token:
            return None
        token_hash = hash_token(raw_token)
        session = self.Session()
        try:
            record = session.query(SessionModel).filter_by(token_hash=token_hash).first()
            if not record:
                return None
            expires_at = datetime.fromisoformat(record.expires_at)
            if datetime.now(timezone.utc) >= expires_at:
                session.delete(record)
                session.commit()
                return None
            user = session.query(UserModel).filter_by(user_id=record.user_id).first()
            if not user or not user.is_active:
                return None
            return AuthenticatedUser(
                user_id=user.user_id,
                username=user.username,
                role=user.role,
                must_change_password=bool(user.must_change_password),
            )
        finally:
            session.close()

    def revoke_session(self, raw_token: str) -> None:
        token_hash = hash_token(raw_token)
        session = self.Session()
        try:
            record = session.query(SessionModel).filter_by(token_hash=token_hash).first()
            if record:
                session.delete(record)
                session.commit()
        finally:
            session.close()

    def revoke_all_sessions_for_user(self, user_id: str) -> None:
        session = self.Session()
        try:
            session.query(SessionModel).filter_by(user_id=user_id).delete()
            session.commit()
        finally:
            session.close()
