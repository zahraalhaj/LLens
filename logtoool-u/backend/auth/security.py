"""
Security primitives: password hashing (bcrypt) and session tokens.

Session tokens are random, high-entropy strings handed to the browser as a
cookie value. Only a SHA-256 hash of the token is ever stored in the
database -- if the database were ever read by someone unauthorized, they'd
get hashes that can't be replayed as a valid cookie, the same principle as
not storing plaintext passwords.
"""
import hashlib
import secrets

import bcrypt

# Bcrypt work factor. 12 is a reasonable default in 2026 for an internal
# tool authenticating ~20 users -- high enough to resist offline cracking
# of a leaked hash, low enough not to make login noticeably slow.
BCRYPT_ROUNDS = 12

SESSION_TOKEN_BYTES = 32  # 256 bits of entropy


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash in the DB -- fail closed, never treat as a match.
        return False


def generate_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
