import time
from datetime import timedelta

import pytest

from backend.auth.security import hash_password, hash_token, verify_password
from backend.auth.service import AuthService, UsernameTakenError


@pytest.fixture
def auth(tmp_path):
    return AuthService(db_path=str(tmp_path / "test.db"), session_lifetime_hours=12)


# -- password hashing -----------------------------------------------------

def test_password_hash_is_not_plaintext():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert h.startswith("$2b$")  # bcrypt hash prefix


def test_verify_password_correct_and_incorrect():
    h = hash_password("hunter2")
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_handles_malformed_hash_gracefully():
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_hash_token_is_deterministic_and_one_way():
    from backend.auth.security import generate_session_token
    token = generate_session_token()
    assert hash_token(token) == hash_token(token)
    assert hash_token(token) != token


# -- user management --------------------------------------------------------

def test_create_user_and_authenticate(auth):
    auth.create_user("alice", "correct-password", role="member")
    result = auth.authenticate("alice", "correct-password")
    assert result is not None
    assert result.username == "alice"
    assert result.role == "member"


def test_authenticate_wrong_password_fails(auth):
    auth.create_user("bob", "correct-password")
    assert auth.authenticate("bob", "wrong-password") is None


def test_authenticate_unknown_user_fails(auth):
    assert auth.authenticate("nobody", "whatever") is None


def test_duplicate_username_rejected(auth):
    auth.create_user("carol", "pw1")
    with pytest.raises(UsernameTakenError):
        auth.create_user("carol", "pw2")


def test_invalid_role_rejected(auth):
    with pytest.raises(ValueError):
        auth.create_user("dave", "pw", role="superuser")


def test_deactivated_user_cannot_authenticate(auth):
    user = auth.create_user("erin", "pw")
    auth.set_user_active(user.user_id, False)
    assert auth.authenticate("erin", "pw") is None


def test_list_users_excludes_password_hash(auth):
    auth.create_user("frank", "pw")
    users = auth.list_users()
    assert len(users) == 1
    assert "password_hash" not in users[0]
    assert users[0]["username"] == "frank"


def test_delete_user_removes_them(auth):
    user = auth.create_user("grace", "pw")
    auth.delete_user(user.user_id)
    assert auth.authenticate("grace", "pw") is None
    assert auth.list_users() == []


# -- sessions ----------------------------------------------------------------

def test_session_created_and_validated(auth):
    user = auth.create_user("heidi", "pw")
    token = auth.create_session(user.user_id)
    validated = auth.validate_session(token)
    assert validated is not None
    assert validated.username == "heidi"


def test_invalid_token_does_not_validate(auth):
    assert auth.validate_session("not-a-real-token") is None


def test_empty_token_does_not_validate(auth):
    assert auth.validate_session("") is None


def test_revoked_session_no_longer_validates(auth):
    user = auth.create_user("ivan", "pw")
    token = auth.create_session(user.user_id)
    auth.revoke_session(token)
    assert auth.validate_session(token) is None


def test_expired_session_does_not_validate(tmp_path):
    # 0-hour lifetime -- session is already expired the instant it's created.
    auth = AuthService(db_path=str(tmp_path / "test.db"), session_lifetime_hours=0)
    user = auth.create_user("judy", "pw")
    token = auth.create_session(user.user_id)
    time.sleep(0.01)
    assert auth.validate_session(token) is None


def test_deactivating_user_revokes_their_sessions(auth):
    user = auth.create_user("kevin", "pw")
    token = auth.create_session(user.user_id)
    assert auth.validate_session(token) is not None
    auth.set_user_active(user.user_id, False)
    assert auth.validate_session(token) is None


def test_changing_password_revokes_existing_sessions(auth):
    user = auth.create_user("laura", "old-pw")
    token = auth.create_session(user.user_id)
    assert auth.validate_session(token) is not None
    auth.change_password(user.user_id, "new-pw")
    assert auth.validate_session(token) is None
    assert auth.authenticate("laura", "new-pw") is not None


def test_deleting_user_cascades_to_sessions(auth):
    user = auth.create_user("mallory", "pw")
    token = auth.create_session(user.user_id)
    auth.delete_user(user.user_id)
    assert auth.validate_session(token) is None


def test_two_users_can_have_concurrent_independent_sessions(auth):
    u1 = auth.create_user("nathan", "pw1")
    u2 = auth.create_user("olivia", "pw2")
    t1 = auth.create_session(u1.user_id)
    t2 = auth.create_session(u2.user_id)
    assert auth.validate_session(t1).username == "nathan"
    assert auth.validate_session(t2).username == "olivia"
    auth.revoke_session(t1)
    assert auth.validate_session(t1) is None
    assert auth.validate_session(t2) is not None  # unaffected
