"""
Unit tests for the embedding client factory and model-listing helper.

Focus areas:
- factory: endpoint propagation through both code paths (dedicated config and
  LLM-fallback). The LLM-fallback path used to drop the endpoint on the floor.
- _list_embedding_models: capability-aware filtering for OpenRouter-style
  responses, "I don't know" passthrough for OpenAI-style responses (no
  modality fields → return all ids; absence does NOT mean "no embeddings"),
  and graceful None on errors.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.routers.llm_config import _list_embedding_models
from src.services.ai_model_service import AIModelService
from src.services.embeddings.factory import get_embedding_config


class TestEndpointPropagation:
    """Endpoint must travel from stored config rows into EmbeddingConfig."""

    @pytest.mark.asyncio
    async def test_explicit_config_passes_endpoint_through(self, db_session):
        service = AIModelService(db_session)
        connection = await service.create_connection(
            name="Embeddings Test",
            provider="openai_compatible",
            api_key="sk-or-test",
            endpoint="https://openrouter.ai/api/v1",
        )
        await service.set_embedding_config(
            connection_id=connection.id,
            model="openai/text-embedding-3-small",
            dimensions=1536,
        )

        config = await get_embedding_config(db_session)
        assert config.endpoint == "https://openrouter.ai/api/v1"
        assert config.api_key == "sk-or-test"

    @pytest.mark.asyncio
    async def test_missing_embedding_config_does_not_fallback_to_llm(self, db_session):
        with pytest.raises(ValueError, match="No embedding configuration"):
            await get_embedding_config(db_session)

    @pytest.mark.asyncio
    async def test_stock_openai_connection_returns_default_endpoint_none(self, db_session):
        service = AIModelService(db_session)
        connection = await service.create_connection(
            name="OpenAI Embeddings",
            provider="openai",
            api_key="sk-openai",
            endpoint=None,
        )
        await service.set_embedding_config(
            connection_id=connection.id,
            model="text-embedding-3-small",
            dimensions=1536,
        )

        config = await get_embedding_config(db_session)
        assert config.endpoint is None


def _httpx_response(payload, status_code: int = 200) -> MagicMock:
    """Build a mock httpx Response."""
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    return response


def _httpx_client_yielding(response_or_exception) -> MagicMock:
    """
    Build a mock httpx.AsyncClient context manager whose .get() returns
    the given response, OR raises the given exception.
    """
    client = MagicMock()
    if isinstance(response_or_exception, BaseException):
        client.get = AsyncMock(side_effect=response_or_exception)
    else:
        client.get = AsyncMock(return_value=response_or_exception)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class TestListEmbeddingModels:
    """Coverage for the capability-aware vs full-list filter."""

    @pytest.mark.asyncio
    async def test_openrouter_capability_aware_filters_to_embeddings(self):
        """OpenRouter exposes output_modalities; we filter to entries with 'embeddings'."""
        payload = {
            "data": [
                {
                    "id": "openai/gpt-4o",
                    "architecture": {"output_modalities": ["text"]},
                },
                {
                    "id": "openai/text-embedding-3-small",
                    "architecture": {"output_modalities": ["embeddings"]},
                },
                {
                    "id": "google/gemini-embedding-2",
                    "architecture": {"output_modalities": ["embeddings"]},
                },
            ]
        }
        with patch(
            "httpx.AsyncClient",
            return_value=_httpx_client_yielding(_httpx_response(payload)),
        ):
            result = await _list_embedding_models("k", "https://openrouter.ai/api/v1")

        assert result == [
            "openai/text-embedding-3-small",
            "google/gemini-embedding-2",
        ]

    @pytest.mark.asyncio
    async def test_openai_no_modality_field_returns_full_list(self):
        """
        OpenAI-style response: no architecture/output_modalities. Absence is NOT
        evidence that no models support embeddings — it means we don't know.
        Return the full id list and let the user pick (test is the gate).
        """
        payload = {
            "data": [
                {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
                {"id": "text-embedding-3-small", "object": "model", "owned_by": "openai"},
                {"id": "text-embedding-ada-002", "object": "model", "owned_by": "openai"},
            ]
        }
        with patch(
            "httpx.AsyncClient",
            return_value=_httpx_client_yielding(_httpx_response(payload)),
        ):
            result = await _list_embedding_models("k", None)

        assert result == ["gpt-4o", "text-embedding-3-small", "text-embedding-ada-002"]

    @pytest.mark.asyncio
    async def test_capability_aware_with_no_embeddings_returns_none(self):
        """
        Endpoint advertises capabilities but lists zero embedding models.
        Returning None is correct here — UI falls back to manual entry.
        """
        payload = {
            "data": [
                {"id": "model-a", "architecture": {"output_modalities": ["text"]}},
                {"id": "model-b", "architecture": {"output_modalities": ["image"]}},
            ]
        }
        with patch(
            "httpx.AsyncClient",
            return_value=_httpx_client_yielding(_httpx_response(payload)),
        ):
            # Real host so SSRF validator passes; httpx still mocked.
            result = await _list_embedding_models("k", "https://api.openai.com/v1")

        assert result is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        """Network/HTTP errors must return None, not raise."""
        with patch(
            "httpx.AsyncClient",
            return_value=_httpx_client_yielding(RuntimeError("boom")),
        ):
            result = await _list_embedding_models("k", "https://api.openai.com/v1")

        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_payload_returns_none(self):
        """If `data` isn't a list, bail."""
        with patch(
            "httpx.AsyncClient",
            return_value=_httpx_client_yielding(_httpx_response({"data": "not a list"})),
        ):
            result = await _list_embedding_models("k", None)

        assert result is None

    @pytest.mark.asyncio
    async def test_mixed_capability_treats_response_as_aware(self):
        """
        If even one entry has output_modalities, treat the whole response as
        capability-aware. Entries that lack the field are skipped (not
        promoted to "include" — we can't classify them).
        """
        payload = {
            "data": [
                {"id": "a", "architecture": {"output_modalities": ["embeddings"]}},
                {"id": "b"},  # No architecture; can't classify; skip
                {"id": "c", "architecture": {"output_modalities": ["text"]}},
            ]
        }
        with patch(
            "httpx.AsyncClient",
            return_value=_httpx_client_yielding(_httpx_response(payload)),
        ):
            # Use a real-resolving host because _list_embedding_models now
            # SSRF-validates the endpoint (DNS lookup + private-IP rejection)
            # before making the request. httpx is still mocked so no network.
            result = await _list_embedding_models("k", "https://api.openai.com/v1")

        assert result == ["a"]

    @pytest.mark.asyncio
    async def test_ssrf_validation_rejects_private_endpoint(self):
        """
        _list_embedding_models must short-circuit on SSRF-rejected endpoints
        (private/loopback/link-local) and never reach httpx. Any reach into
        httpx here would be a regression: the validator failed open.
        """
        with patch(
            "httpx.AsyncClient",
            return_value=_httpx_client_yielding(
                _httpx_response({"data": [{"id": "should-not-see"}]})
            ),
        ) as httpx_factory:
            # Hostname doesn't resolve and isn't allowlisted → ValueError
            # inside the validator → _list_embedding_models returns None.
            result = await _list_embedding_models(
                "k", "http://nope.invalid.local/v1"
            )

        assert result is None
        # If we ever reach httpx with an SSRF-rejected URL, the validator
        # has failed open. This catches that regression cleanly.
        httpx_factory.assert_not_called()
