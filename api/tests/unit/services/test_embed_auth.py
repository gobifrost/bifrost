"""Unit tests for HMAC embed verification."""

import base64
import hashlib
import hmac as hmac_module

import pytest

from src.services.embed_auth import (
    SCHEME_HALOPSA,
    SCHEME_SHOPIFY,
    compute_embed_hmac,
    verified_embed_context,
)


class TestComputeEmbedHmac:
    def test_single_param(self):
        result = compute_embed_hmac({"agent_id": "42"}, "my-secret")
        expected = hmac_module.new(
            b"my-secret", b"agent_id=42", hashlib.sha256
        ).hexdigest()
        assert result == expected

    def test_multiple_params_sorted(self):
        """Params should be sorted alphabetically by key."""
        result = compute_embed_hmac(
            {"ticket_id": "1001", "agent_id": "42"}, "my-secret"
        )
        expected = hmac_module.new(
            b"my-secret", b"agent_id=42&ticket_id=1001", hashlib.sha256
        ).hexdigest()
        assert result == expected

    def test_empty_params(self):
        result = compute_embed_hmac({}, "my-secret")
        expected = hmac_module.new(
            b"my-secret", b"", hashlib.sha256
        ).hexdigest()
        assert result == expected


class TestVerifiedEmbedContextShopify:
    def test_valid_hmac(self):
        secret = "test-secret"
        params = {"agent_id": "42", "ticket_id": "1001"}
        valid_hmac = compute_embed_hmac(params, secret)
        params_with_hmac = {**params, "hmac": valid_hmac}
        assert verified_embed_context(params_with_hmac, secret, SCHEME_SHOPIFY) == params

    def test_invalid_hmac(self):
        params = {"agent_id": "42", "hmac": "invalid-garbage"}
        assert verified_embed_context(params, "test-secret", SCHEME_SHOPIFY) is None

    def test_tampered_param(self):
        secret = "test-secret"
        valid_hmac = compute_embed_hmac({"agent_id": "42"}, secret)
        tampered = {"agent_id": "99", "hmac": valid_hmac}
        assert verified_embed_context(tampered, secret, SCHEME_SHOPIFY) is None

    def test_missing_hmac_param(self):
        assert (
            verified_embed_context({"agent_id": "42"}, "test-secret", SCHEME_SHOPIFY)
            is None
        )


class TestVerifiedEmbedContextHalopsa:
    @staticmethod
    def _halo_sig(agent_id: str, secret: str) -> str:
        digest = hmac_module.new(
            secret.encode(), agent_id.encode(), hashlib.sha256
        ).digest()
        return base64.b64encode(digest).decode()

    def test_valid_hmac(self):
        secret = "halo-shared"
        agent_id = "42"
        sig = self._halo_sig(agent_id, secret)
        params = {"agent_id": agent_id, "ticket_id": "1001", "hmac": sig}
        assert (
            verified_embed_context(params, secret, SCHEME_HALOPSA)
            == {"agent_id": agent_id}
        )

    def test_extra_params_do_not_invalidate(self):
        """HaloPSA scheme signs only agent_id; other params are not covered."""
        secret = "halo-shared"
        sig = self._halo_sig("42", secret)
        params = {"agent_id": "42", "anything": "else", "hmac": sig}
        assert (
            verified_embed_context(params, secret, SCHEME_HALOPSA)
            == {"agent_id": "42"}
        )

    def test_tampered_agent_id(self):
        secret = "halo-shared"
        sig = self._halo_sig("42", secret)
        tampered = {"agent_id": "99", "hmac": sig}
        assert verified_embed_context(tampered, secret, SCHEME_HALOPSA) is None

    def test_missing_agent_id(self):
        assert (
            verified_embed_context(
                {"hmac": "anything"}, "halo-shared", SCHEME_HALOPSA
            )
            is None
        )

    def test_missing_hmac(self):
        assert (
            verified_embed_context(
                {"agent_id": "42"}, "halo-shared", SCHEME_HALOPSA
            )
            is None
        )

    def test_shopify_signature_rejected(self):
        """A valid Shopify-style HMAC must NOT verify under the Halo scheme."""
        secret = "shared"
        params = {"agent_id": "42"}
        shopify_sig = compute_embed_hmac(params, secret)
        with_sig = {**params, "hmac": shopify_sig}
        assert verified_embed_context(with_sig, secret, SCHEME_HALOPSA) is None


class TestSchemeDispatch:
    def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError, match="Unknown HMAC scheme"):
            verified_embed_context({"hmac": "x"}, "s", "made-up-scheme")

    def test_halo_signature_rejected_under_shopify(self):
        secret = "shared"
        digest = hmac_module.new(
            secret.encode(), b"42", hashlib.sha256
        ).digest()
        halo_sig = base64.b64encode(digest).decode()
        params = {"agent_id": "42", "hmac": halo_sig}
        assert verified_embed_context(params, secret, SCHEME_SHOPIFY) is None


class TestVerifiedEmbedContextProjection:
    def test_shopify_promotes_every_signed_query_field(self):
        secret = "contoso-shopify-secret"
        params = {"agent_id": "42", "customer": "Fabrikam"}
        context = verified_embed_context(
            {**params, "hmac": compute_embed_hmac(params, secret)},
            secret,
            SCHEME_SHOPIFY,
        )

        assert context == params

    def test_halopsa_promotes_only_agent_id(self):
        secret = "contoso-halopsa-secret"
        agent_id = "42"
        digest = hmac_module.new(
            secret.encode(), agent_id.encode(), hashlib.sha256
        ).digest()
        context = verified_embed_context(
            {
                "agent_id": agent_id,
                "customer": "Fabrikam",
                "hmac": base64.b64encode(digest).decode(),
            },
            secret,
            SCHEME_HALOPSA,
        )

        assert context == {"agent_id": agent_id}
