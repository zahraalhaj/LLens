"""
Auth data models. Deliberately shares the same SQLAlchemy declarative Base
as backend/core/store.py -- there's one SQLite file for this app, and
Base.metadata.create_all() picks up every model registered against Base
regardless of which module defined it, so importing this module is enough
to get the users/sessions tables created alongside events/batches.
"""
from sqlalchemy import Column, ForeignKey, Integer, String

from backend.core.store import Base


class UserModel(Base):
    __tablename__ = "users"

    user_id = Column(String, primary_key=True)
    username = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="member")  # 'admin' or 'member'
    created_at = Column(String, nullable=False)
    is_active = Column(Integer, nullable=False, default=1)  # SQLite has no bool type
    # True whenever an admin sets/resets this user's password -- cleared the
    # moment the user successfully sets their own new password. Enforced at
    # the API layer (see api/deps.py), not just a UI hint.
    must_change_password = Column(Integer, nullable=False, default=1)


class SessionModel(Base):
    __tablename__ = "sessions"

    session_id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    created_at = Column(String, nullable=False)
    expires_at = Column(String, nullable=False)
