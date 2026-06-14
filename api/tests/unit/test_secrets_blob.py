import json

from src.services.solutions.secrets_blob import (
    BLOB_VERSION,
    SolutionContent,
    decode_secrets_blob,
    encode_secrets_blob,
)


def test_blob_roundtrips_values_and_data():
    content = SolutionContent(
        config_values={"api_key": "xyz", "region": "us-east"},
        table_data={"widgets": [{"id": 1, "name": "a"}]},
    )
    blob = encode_secrets_blob(content, password="pw")
    out = decode_secrets_blob(blob, password="pw")
    assert out.config_values == content.config_values
    assert out.table_data == content.table_data


def test_wrong_password_raises():
    import pytest
    from cryptography.fernet import InvalidToken

    blob = encode_secrets_blob(SolutionContent(config_values={"a": "b"}), password="A")
    with pytest.raises(InvalidToken):
        decode_secrets_blob(blob, password="B")


def test_envelope_is_self_describing():
    """The cleartext envelope carries version + scrypt params + salt so decode
    can reproduce the key without out-of-band state."""
    blob = encode_secrets_blob(SolutionContent(config_values={"a": "b"}), password="pw")
    env = json.loads(blob)
    assert env["v"] == BLOB_VERSION
    assert env["kdf"] == "scrypt"
    assert env["salt"]  # base64 salt present
    assert env["n"] and env["r"] and env["p"]
    assert env["ciphertext"]


def test_per_export_salt_differs():
    """Two exports of identical content + password must differ — proving a fresh
    random salt per export (otherwise a leaked zip's KDF is reusable/precomputable)."""
    content = SolutionContent(config_values={"a": "b"})
    blob1 = encode_secrets_blob(content, password="same-pw")
    blob2 = encode_secrets_blob(content, password="same-pw")
    assert blob1 != blob2

    env1 = json.loads(blob1)
    env2 = json.loads(blob2)
    assert env1["salt"] != env2["salt"]  # different salt
    assert env1["ciphertext"] != env2["ciphertext"]  # different ciphertext

    # Both still decode correctly with the shared password.
    assert decode_secrets_blob(blob1, password="same-pw").config_values == {"a": "b"}
    assert decode_secrets_blob(blob2, password="same-pw").config_values == {"a": "b"}
