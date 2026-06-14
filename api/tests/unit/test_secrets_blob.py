from src.services.solutions.secrets_blob import (
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
