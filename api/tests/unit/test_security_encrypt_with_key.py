from src.core.security import encrypt_with_key, decrypt_with_key


def test_encrypt_decrypt_with_password_roundtrips():
    token = encrypt_with_key("s3cret-value", "correct horse battery staple")
    assert decrypt_with_key(token, "correct horse battery staple") == "s3cret-value"


def test_wrong_password_fails():
    import pytest
    from cryptography.fernet import InvalidToken
    token = encrypt_with_key("v", "pw-A")
    with pytest.raises(InvalidToken):
        decrypt_with_key(token, "pw-B")
