import pytest

from backend.auth.service import AuthService, InvalidCurrentPasswordError


@pytest.fixture
def auth(tmp_path):
    return AuthService(db_path=str(tmp_path / "test.db"), session_lifetime_hours=12)


def test_new_user_must_change_password_by_default(auth):
    user = auth.create_user("alice", "temp-password-123")
    assert user.must_change_password is True


def test_authenticate_reports_must_change_password(auth):
    auth.create_user("bob", "temp-password-123")
    result = auth.authenticate("bob", "temp-password-123")
    assert result is not None
    assert result.must_change_password is True


def test_validate_session_reports_must_change_password(auth):
    user = auth.create_user("carol", "temp-password-123")
    token = auth.create_session(user.user_id)
    validated = auth.validate_session(token)
    assert validated.must_change_password is True


def test_self_change_password_clears_the_flag(auth):
    user = auth.create_user("dave", "temp-password-123")
    new_token = auth.self_change_password(user.user_id, "temp-password-123", "a-real-new-password")
    validated = auth.validate_session(new_token)
    assert validated.must_change_password is False


def test_self_change_password_requires_correct_current_password(auth):
    user = auth.create_user("erin", "temp-password-123")
    with pytest.raises(InvalidCurrentPasswordError):
        auth.self_change_password(user.user_id, "wrong-current-password", "a-real-new-password")


def test_self_change_password_actually_changes_the_password(auth):
    user = auth.create_user("frank", "temp-password-123")
    auth.self_change_password(user.user_id, "temp-password-123", "a-real-new-password")
    assert auth.authenticate("frank", "temp-password-123") is None
    result = auth.authenticate("frank", "a-real-new-password")
    assert result is not None
    assert result.must_change_password is False


def test_self_change_password_revokes_old_sessions(auth):
    user = auth.create_user("grace", "temp-password-123")
    old_token = auth.create_session(user.user_id)
    auth.self_change_password(user.user_id, "temp-password-123", "a-real-new-password")
    assert auth.validate_session(old_token) is None


def test_self_change_password_issues_a_usable_new_session(auth):
    user = auth.create_user("heidi", "temp-password-123")
    new_token = auth.self_change_password(user.user_id, "temp-password-123", "a-real-new-password")
    validated = auth.validate_session(new_token)
    assert validated is not None
    assert validated.username == "heidi"


def test_admin_change_password_also_sets_must_change_password(auth):
    """Admin-initiated resets (e.g. 'forgot password') should also expire,
    same as initial account creation -- not a permanent password an admin
    knows indefinitely."""
    user = auth.create_user("ivan", "temp-password-123")
    auth.self_change_password(user.user_id, "temp-password-123", "ivans-own-password")
    auth.change_password(user.user_id, "admin-reset-password")
    result = auth.authenticate("ivan", "admin-reset-password")
    assert result.must_change_password is True


def test_force_password_reset_without_changing_password(auth):
    user = auth.create_user("judy", "temp-password-123")
    auth.self_change_password(user.user_id, "temp-password-123", "judys-own-password")
    assert auth.authenticate("judy", "judys-own-password").must_change_password is False

    auth.force_password_reset(user.user_id)
    result = auth.authenticate("judy", "judys-own-password")  # password itself is unchanged
    assert result is not None
    assert result.must_change_password is True


def test_force_password_reset_revokes_existing_sessions(auth):
    user = auth.create_user("kevin", "temp-password-123")
    token = auth.self_change_password(user.user_id, "temp-password-123", "kevins-password")
    assert auth.validate_session(token) is not None
    auth.force_password_reset(user.user_id)
    assert auth.validate_session(token) is None


def test_list_users_includes_must_change_password(auth):
    auth.create_user("laura", "temp-password-123")
    users = auth.list_users()
    assert users[0]["must_change_password"] is True


def test_migration_adds_column_to_existing_database(tmp_path):
    """Simulates a database created before this field existed -- the
    migration should add the column without losing data."""
    db_path = str(tmp_path / "legacy.db")
    auth1 = AuthService(db_path=db_path)
    user = auth1.create_user("mallory", "temp-password-123")

    # Manually strip the column back off, simulating a pre-migration DB.
    from sqlalchemy import create_engine, text
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE users RENAME TO users_old"))
        conn.execute(
            text(
                "CREATE TABLE users (user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, "
                "password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL, is_active INTEGER NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (user_id, username, password_hash, role, created_at, is_active) "
                "SELECT user_id, username, password_hash, role, created_at, is_active FROM users_old"
            )
        )
        conn.execute(text("DROP TABLE users_old"))
        conn.commit()
    engine.dispose()

    # Re-instantiating AuthService against this "legacy" DB should migrate cleanly.
    auth2 = AuthService(db_path=db_path)
    result = auth2.authenticate("mallory", "temp-password-123")
    assert result is not None
    assert result.must_change_password is True  # safe default for pre-existing rows
