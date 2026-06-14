"""The password-encrypt path that used to live in src.core.security.encrypt_with_key
(HKDF + fixed salt) was replaced by the scrypt + per-export-salt blob codec in
src.services.solutions.secrets_blob. These tests pin the password round-trip and
wrong-password behavior of the surviving public surface."""

from src.services.solutions.secrets_blob import (
    SolutionContent,
    decode_secrets_blob,
    encode_secrets_blob,
)


def test_encrypt_decrypt_with_password_roundtrips():
    blob = encode_secrets_blob(
        SolutionContent(config_values={"k": "s3cret-value"}),
        password="correct horse battery staple",
    )
    out = decode_secrets_blob(blob, password="correct horse battery staple")
    assert out.config_values == {"k": "s3cret-value"}


def test_wrong_password_fails():
    import pytest
    from cryptography.fernet import InvalidToken

    blob = encode_secrets_blob(SolutionContent(config_values={"k": "v"}), password="pw-A")
    with pytest.raises(InvalidToken):
        decode_secrets_blob(blob, password="pw-B")
