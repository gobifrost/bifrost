"""Tests for secret re-encryption between Bifrost instances."""

import pytest
from src.core.security import encrypt_secret, decrypt_secret, decrypt_with_key, derive_fernet_key


def test_derive_fernet_key_produces_valid_key():
    """derive_fernet_key with explicit key returns 44-byte base64."""
    key = derive_fernet_key("source-secret-key-1234")
    assert len(key) == 44  # base64-encoded 32 bytes


def test_decrypt_with_key_roundtrips():
    """Encrypt with current instance, decrypt with explicit key matching current instance."""
    from src.config import get_settings
    settings = get_settings()
    encrypted = encrypt_secret("my-api-key-123")
    plaintext = decrypt_with_key(encrypted, settings.secret_key)
    assert plaintext == "my-api-key-123"


def test_decrypts_ciphertext_created_with_cryptography_47():
    """Upgrading the crypto dependency must preserve existing stored secrets.

    Generated with cryptography 47.0.0 and the existing Bifrost HKDF/Fernet
    format, using only the public synthetic key below.
    """
    ciphertext = (
        "Z0FBQUFBQnE4eTczbHFLTFN0VnN5VlMzWENXenZsTG93SDZYb3lKVXc5SUk4YkVT"
        "NGF6S0JFTjhNakJZZUlTY3ZBdHJYSkRnam1ZbG5kVndBbF84Y3RvMXh5NnFkakZSbzB3"
        "eFI0QVBoUF9EaGNvaHFmSDU4TzA9"
    )
    assert decrypt_with_key(ciphertext, "public-compatibility-test-key") == "stored-before-upgrade"


def test_decrypt_with_key_wrong_key_fails():
    """Decrypting with wrong key raises an error."""
    encrypted = encrypt_secret("my-api-key-123")
    with pytest.raises(Exception):
        decrypt_with_key(encrypted, "wrong-key")


def test_cross_instance_reencrypt():
    """Simulate export from instance A, import to instance B."""
    import base64
    from cryptography.fernet import Fernet

    source_key = "source-instance-secret-key-abcdef"

    # Encrypt as if on source instance
    source_fernet = derive_fernet_key(source_key)
    f = Fernet(source_fernet)
    encrypted_on_source = base64.urlsafe_b64encode(f.encrypt(b"secret-value")).decode()

    # Decrypt with source creds, verify plaintext
    plaintext = decrypt_with_key(encrypted_on_source, source_key)
    assert plaintext == "secret-value"

    # Re-encrypt with destination (current instance)
    re_encrypted = encrypt_secret(plaintext)

    # Verify destination can decrypt
    assert decrypt_secret(re_encrypted) == "secret-value"
