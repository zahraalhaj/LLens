import pytest

from backend.remote import crypto


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR_NAME, crypto.generate_key())


def test_encrypt_then_decrypt_roundtrips():
    ciphertext = crypto.encrypt_secret("super-secret-password")
    assert ciphertext != "super-secret-password"
    assert crypto.decrypt_secret(ciphertext) == "super-secret-password"


def test_encrypted_value_is_not_plaintext_substring():
    secret = "my-ssh-private-key-material"
    ciphertext = crypto.encrypt_secret(secret)
    assert secret not in ciphertext


def test_missing_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv(crypto.ENV_VAR_NAME, raising=False)
    with pytest.raises(crypto.EncryptionKeyMissingError):
        crypto.encrypt_secret("anything")


def test_invalid_key_format_raises_clear_error(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR_NAME, "not-a-valid-fernet-key")
    with pytest.raises(crypto.EncryptionKeyMissingError):
        crypto.encrypt_secret("anything")


def test_decrypt_with_wrong_key_fails_clearly(monkeypatch):
    ciphertext = crypto.encrypt_secret("secret-value")
    monkeypatch.setenv(crypto.ENV_VAR_NAME, crypto.generate_key())  # different key
    with pytest.raises(crypto.DecryptionFailedError):
        crypto.decrypt_secret(ciphertext)


def test_generate_key_produces_usable_keys():
    key = crypto.generate_key()
    assert isinstance(key, str)
    assert len(key) > 0
