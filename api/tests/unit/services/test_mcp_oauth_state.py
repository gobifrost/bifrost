"""Unit tests for ``mcp_client.oauth_state``.

Covers the encode/decode round trip, signature/expiry tampering, the
PKCE challenge derivation, and the (optional) Redis nonce path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import jwt
import pytest

from src.config import get_settings
from src.services.mcp_client.oauth_state import (
    StateDecodeError,
    decode_state,
    encode_state,
    generate_pkce_verifier,
    pkce_challenge_for,
)


class TestPKCE:
    def test_verifier_length_in_rfc_range(self):
        v = generate_pkce_verifier()
        assert 43 <= len(v) <= 128

    def test_challenge_is_base64url_no_padding(self):
        v = generate_pkce_verifier()
        c = pkce_challenge_for(v)
        # base64url alphabet, no '='
        assert "=" not in c
        # 43 chars = sha256(32 bytes) base64url'd without padding
        assert len(c) == 43


class TestEncodeDecodeRoundTrip:
    def test_service_flow_round_trip(self):
        connection_id = uuid4()
        verifier = generate_pkce_verifier()
        token, nonce = encode_state(
            connection_id=connection_id,
            flow_type="service",
            pkce_verifier=verifier,
            redirect_uri="https://example.com/cb",
        )
        payload = decode_state(token)
        assert payload["connection_id"] == str(connection_id)
        assert payload["flow_type"] == "service"
        assert payload["pkce_verifier"] == verifier
        assert payload["nonce"] == nonce
        assert payload["redirect_uri"] == "https://example.com/cb"
        assert payload["code_challenge"] == pkce_challenge_for(verifier)
        assert "user_id" not in payload

    def test_user_flow_round_trip(self):
        connection_id = uuid4()
        user_id = uuid4()
        verifier = generate_pkce_verifier()
        token, _ = encode_state(
            connection_id=connection_id,
            flow_type="user",
            pkce_verifier=verifier,
            user_id=user_id,
        )
        payload = decode_state(token)
        assert payload["flow_type"] == "user"
        assert payload["user_id"] == str(user_id)

    def test_user_flow_requires_user_id(self):
        with pytest.raises(ValueError, match="user_id is required"):
            encode_state(
                connection_id=uuid4(),
                flow_type="user",
                pkce_verifier=generate_pkce_verifier(),
            )

    def test_service_flow_rejects_user_id(self):
        with pytest.raises(ValueError, match="user_id must be None"):
            encode_state(
                connection_id=uuid4(),
                flow_type="service",
                pkce_verifier=generate_pkce_verifier(),
                user_id=uuid4(),
            )


class TestDecodeFailures:
    def test_garbage_token(self):
        with pytest.raises(StateDecodeError):
            decode_state("not-a-jwt")

    def test_signed_with_wrong_key(self):
        settings = get_settings()
        # Sign with a different secret to simulate tampering
        bogus = jwt.encode(
            {
                "iss": settings.jwt_issuer,
                "aud": "bifrost-mcp-oauth-state",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
                "nonce": "x",
                "connection_id": str(uuid4()),
                "flow_type": "service",
                "pkce_verifier": "v",
            },
            "different-secret-key-32-chars-long-x",
            algorithm=settings.algorithm,
        )
        with pytest.raises(StateDecodeError):
            decode_state(bogus)

    def test_expired(self):
        settings = get_settings()
        expired = jwt.encode(
            {
                "iss": settings.jwt_issuer,
                "aud": "bifrost-mcp-oauth-state",
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
                "nonce": "x",
                "connection_id": str(uuid4()),
                "flow_type": "service",
                "pkce_verifier": "v",
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        with pytest.raises(StateDecodeError, match="expired"):
            decode_state(expired)

    def test_missing_required_field(self):
        settings = get_settings()
        bad = jwt.encode(
            {
                "iss": settings.jwt_issuer,
                "aud": "bifrost-mcp-oauth-state",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
                # nonce missing
                "connection_id": str(uuid4()),
                "flow_type": "service",
                "pkce_verifier": "v",
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        with pytest.raises(StateDecodeError, match="nonce"):
            decode_state(bad)

    def test_invalid_flow_type(self):
        settings = get_settings()
        bad = jwt.encode(
            {
                "iss": settings.jwt_issuer,
                "aud": "bifrost-mcp-oauth-state",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
                "nonce": "n",
                "connection_id": str(uuid4()),
                "flow_type": "weirdo",
                "pkce_verifier": "v",
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        with pytest.raises(StateDecodeError, match="invalid flow_type"):
            decode_state(bad)

    def test_user_flow_missing_user_id(self):
        settings = get_settings()
        bad = jwt.encode(
            {
                "iss": settings.jwt_issuer,
                "aud": "bifrost-mcp-oauth-state",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
                "nonce": "n",
                "connection_id": str(uuid4()),
                "flow_type": "user",
                "pkce_verifier": "v",
                # user_id missing
            },
            settings.secret_key,
            algorithm=settings.algorithm,
        )
        with pytest.raises(StateDecodeError, match="user_id"):
            decode_state(bad)


class TestNonceTracking:
    """Best-effort Redis path — verify it gracefully degrades when Redis
    is unreachable rather than crashing the connect flow."""

    @pytest.mark.asyncio
    async def test_remember_and_consume_nonce(self):
        """Round trip works against the test stack's real Redis."""
        from src.services.mcp_client.oauth_state import (
            consume_nonce,
            remember_nonce,
        )

        nonce = f"test-nonce-{uuid4().hex}"
        await remember_nonce(nonce)
        assert await consume_nonce(nonce) is True
        # Replay rejected
        assert await consume_nonce(nonce) is False

    @pytest.mark.asyncio
    async def test_consume_nonce_redis_unreachable_returns_false(self):
        from src.services.mcp_client import oauth_state

        with patch(
            "src.core.redis_client.get_redis_client",
            side_effect=RuntimeError("redis down"),
        ):
            assert await oauth_state.consume_nonce("any") is False

    @pytest.mark.asyncio
    async def test_remember_nonce_redis_unreachable_does_not_raise(self):
        from src.services.mcp_client import oauth_state

        with patch(
            "src.core.redis_client.get_redis_client",
            side_effect=RuntimeError("redis down"),
        ):
            # Must not raise — degrades to "no replay protection"
            await oauth_state.remember_nonce("any")
