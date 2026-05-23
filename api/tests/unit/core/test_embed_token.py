"""Unit tests for embed token creation and validation."""

from uuid import uuid4

from src.core.security import create_embed_token, decode_token


class TestEmbedToken:
    def test_create_and_decode(self):
        app_id = str(uuid4())
        org_id = str(uuid4())
        verified_params = {"agent_id": "42", "ticket_id": "1001"}

        token = create_embed_token(
            app_id=app_id,
            org_id=org_id,
            verified_params=verified_params,
        )

        payload = decode_token(token, expected_type="embed")
        assert payload is not None
        assert payload["app_id"] == app_id
        assert payload["org_id"] == org_id
        assert payload["verified_params"] == verified_params
        assert payload["embed"] is True
        assert payload["is_superuser"] is False
        assert payload["type"] == "embed"

    def test_embed_token_rejected_as_access(self):
        """Embed tokens must NOT be accepted as access tokens."""
        token = create_embed_token(
            app_id=str(uuid4()),
            org_id=str(uuid4()),
            verified_params={},
        )
        payload = decode_token(token, expected_type="access")
        assert payload is None
