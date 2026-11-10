"""Unit tests for passkey service helpers."""

from webauthn.helpers.structs import AuthenticatorTransport
from webauthn import generate_registration_options, options_to_json
from webauthn.helpers.structs import PublicKeyCredentialDescriptor

from src.services.passkey_service import _normalize_transports


def test_passkey_assertion_verification_with_crypto_dependencies():
    """A real signed assertion survives the crypto/OpenSSL dependency upgrade."""
    import base64
    import hashlib
    import json

    import cbor2
    import pytest
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from webauthn import verify_authentication_response
    from webauthn.helpers.exceptions import InvalidAuthenticationResponse

    def b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_numbers()
    credential_key = cbor2.dumps({
        1: 2, 3: -7, -1: 1,
        -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big"),
    })
    challenge = b"public-passkey-compatibility-challenge"
    client_data = json.dumps({
        "type": "webauthn.get", "challenge": b64(challenge),
        "origin": "https://bifrost.example.com",
    }).encode()
    authenticator_data = hashlib.sha256(b"bifrost.example.com").digest() + b"\x05\x00\x00\x00\x01"
    signature = key.sign(
        authenticator_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()),
    )
    response = {
        "authenticatorData": b64(authenticator_data),
        "clientDataJSON": b64(client_data), "signature": b64(signature),
    }
    credential = {
        "id": b64(b"credential"), "rawId": b64(b"credential"), "type": "public-key",
        "response": response,
    }

    def verify():
        return verify_authentication_response(
            credential=credential,
            expected_challenge=challenge, expected_rp_id="bifrost.example.com",
            expected_origin="https://bifrost.example.com", credential_public_key=credential_key,
            credential_current_sign_count=0, require_user_verification=True,
        )

    result = verify()
    assert result.new_sign_count == 1
    response["signature"] = b64(b"invalid-signature")
    with pytest.raises(InvalidAuthenticationResponse):
        verify()


def test_normalize_transports_converts_stored_strings_to_webauthn_enums():
    """Stored JSONB strings should be safe to pass back to py_webauthn."""
    transports = _normalize_transports(["internal", "hybrid"])

    assert transports == [
        AuthenticatorTransport.INTERNAL,
        AuthenticatorTransport.HYBRID,
    ]


def test_normalize_transports_ignores_unknown_or_empty_values():
    """Unexpected stored transport values should not break passkey registration."""
    transports = _normalize_transports(["internal", "", None, "future-transport"])

    assert transports == [AuthenticatorTransport.INTERNAL]


def test_normalized_transports_are_safe_for_registration_options_serialization():
    """Regression for py_webauthn expecting descriptor transports as enums."""
    options = generate_registration_options(
        rp_id="bifrost.example.com",
        rp_name="Bifrost",
        user_id=b"test-user-handle",
        user_name="user@example.com",
        user_display_name="User",
        exclude_credentials=[
            PublicKeyCredentialDescriptor(
                id=b"existing-credential-id",
                transports=_normalize_transports(["internal"]),
            )
        ],
    )

    serialized = options_to_json(options)

    assert '"transports": ["internal"]' in serialized
