"""
Encrypts remote-machine credentials (passwords / private keys) at rest.

Unlike SMTP credentials (a single static config read from env vars), these
are per-machine secrets an admin enters dynamically through the UI, so they
have to live in the database -- encryption at rest is the mitigation here,
not "don't store it in the app at all" (which isn't an option for a feature
whose entire point is storing per-machine connection info).

The encryption key itself is a server-side secret from an environment
variable, never committed, never derived from anything guessable. If it's
missing, the app fails loudly at first use rather than silently storing
plaintext -- a missing key is a deployment misconfiguration that should be
fixed, not silently downgraded.
"""
import base64
import os

from cryptography.fernet import Fernet, InvalidToken

ENV_VAR_NAME = "REMOTE_MACHINES_ENCRYPTION_KEY"


class EncryptionKeyMissingError(Exception):
    pass


class DecryptionFailedError(Exception):
    pass


def _get_fernet() -> Fernet:
    key = os.environ.get(ENV_VAR_NAME)
    if not key:
        raise EncryptionKeyMissingError(
            f"{ENV_VAR_NAME} is not set. Generate one with "
            f"`python -m backend.remote.crypto generate-key` and set it as an "
            f"environment variable before configuring any remote machines. "
            f"This key encrypts stored machine passwords/private keys -- "
            f"losing it means every stored credential becomes unrecoverable "
            f"(machines would need to be re-added), so back it up like any "
            f"other production secret."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as e:
        raise EncryptionKeyMissingError(f"{ENV_VAR_NAME} is not a valid Fernet key: {e}")


def generate_key() -> str:
    return Fernet.generate_key().decode("utf-8")


def encrypt_secret(plaintext: str) -> str:
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise DecryptionFailedError(
            "Failed to decrypt a stored credential -- the encryption key may have "
            "changed since it was stored, or the data is corrupted."
        )


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 2 and sys.argv[1] == "generate-key":
        print(generate_key())
    else:
        print(f"Usage: python -m backend.remote.crypto generate-key", file=sys.stderr)
        sys.exit(1)
