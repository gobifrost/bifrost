"""Unit tests for passkey service helpers."""

from webauthn.helpers.structs import AuthenticatorTransport
from webauthn import generate_registration_options, options_to_json
from webauthn.helpers.structs import PublicKeyCredentialDescriptor

from src.services.passkey_service import _normalize_transports


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
