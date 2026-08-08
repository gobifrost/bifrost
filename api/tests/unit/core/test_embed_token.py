"""Unit tests for typed embed access-token creation."""
from uuid import uuid4

from src.core.security import create_embed_access_token, decode_token


class TestEmbedAccessToken:
    def test_form_hmac_claims_are_typed_and_context_is_namespaced(self):
        form_id = str(uuid4())
        org_id = str(uuid4())
        verified_context = {"agent_id": "42", "ticket_id": "1001"}

        token = create_embed_access_token(
            embed_kind="form",
            grant="hmac",
            resource_id=form_id,
            org_id=org_id,
            verified_context=verified_context,
        )

        payload = decode_token(token, expected_type="access")
        assert payload is not None
        assert payload["embed"] is True
        assert payload["embed_kind"] == "form"
        assert payload["grant"] == "hmac"
        assert payload["form_id"] == form_id
        assert payload["org_id"] == org_id
        assert payload["verified_context"] == verified_context
        assert "verified_params" not in payload
        assert payload["is_external"] is True
        assert payload["is_superuser"] is False
        assert payload["jti"]

    def test_app_hmac_keeps_legacy_verified_params_for_compatibility(self):
        app_id = str(uuid4())
        verified_context = {"ticket_id": "1001"}

        token = create_embed_access_token(
            embed_kind="app",
            grant="hmac",
            resource_id=app_id,
            org_id=str(uuid4()),
            verified_context=verified_context,
        )

        payload = decode_token(token, expected_type="access")
        assert payload is not None
        assert payload["app_id"] == app_id
        assert payload["verified_context"] == verified_context
        assert payload["verified_params"] == verified_context

    def test_public_form_token_carries_capability_fingerprint(self):
        fingerprint = "a" * 64
        token = create_embed_access_token(
            embed_kind="form",
            grant="public",
            resource_id=str(uuid4()),
            org_id=str(uuid4()),
            display_name="Public Form · Contact us",
            verified_context={},
            capability_fingerprint=fingerprint,
        )

        payload = decode_token(token, expected_type="access")
        assert payload is not None
        assert payload["grant"] == "public"
        assert payload["name"] == "Public Form · Contact us"
        assert payload["capability_fingerprint"] == fingerprint
