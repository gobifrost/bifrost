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

import base64
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from src.routers.llm_config import _list_embedding_models
from src.services.embeddings.factory import (
    EMBEDDING_CONFIG_CATEGORY,
    EMBEDDING_CONFIG_KEY,
    LLM_CONFIG_CATEGORY,
    LLM_CONFIG_KEY,
    get_embedding_config,
)


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.secret_key = "test-secret-key-for-testing-must-be-32-chars"
    return settings


@pytest.fixture
def fernet_instance(mock_settings):
    key_bytes = mock_settings.secret_key.encode()[:32].ljust(32, b"0")
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def _system_config_row(category: str, key: str, value_json: dict) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.category = category
    row.key = key
    row.value_json = value_json
    row.organization_id = None
    return row


def _make_session(rows_in_order: list) -> AsyncMock:
    """
    Build an AsyncMock session whose .execute() returns successive results.

    The factory queries: 1) embedding config, 2) LLM config (if it falls through).
    Each entry in rows_in_order is the .scalars().first() return for one query.
    """
    session = AsyncMock()
    results = []
    for row in rows_in_order:
        result = MagicMock()
        result.scalars.return_value.first.return_value = row
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


class TestEndpointPropagation:
    """Endpoint must travel from stored config rows into EmbeddingConfig."""

    @pytest.mark.asyncio
    async def test_dedicated_config_passes_endpoint_through(
        self, mock_settings, fernet_instance
    ):
        encrypted_key = fernet_instance.encrypt(b"sk-or-test").decode()
        embedding_row = _system_config_row(
            EMBEDDING_CONFIG_CATEGORY,
            EMBEDDING_CONFIG_KEY,
            {
                "model": "text-embedding-3-small",
                "encrypted_api_key": encrypted_key,
                "endpoint": "https://openrouter.ai/api/v1",
            },
        )
        session = _make_session([embedding_row])

        with patch("src.services.embeddings.factory.get_settings", return_value=mock_settings):
            config = await get_embedding_config(session)

        assert config.endpoint == "https://openrouter.ai/api/v1"
        assert config.api_key == "sk-or-test"

    @pytest.mark.asyncio
    async def test_llm_fallback_refuses_custom_endpoint(self, mock_settings, fernet_instance):
        # No dedicated embedding row, LLM has a custom endpoint (OpenRouter).
        # Fallback must REFUSE — `text-embedding-3-small` is not a valid
        # model id on non-OpenAI endpoints, so silently using it would just
        # break at first embed call.
        encrypted_key = fernet_instance.encrypt(b"sk-or-llm").decode()
        llm_row = _system_config_row(
            LLM_CONFIG_CATEGORY,
            LLM_CONFIG_KEY,
            {
                "provider": "openai",
                "model": "deepseek/v4-flash",
                "encrypted_api_key": encrypted_key,
                "endpoint": "https://openrouter.ai/api/v1",
            },
        )
        session = _make_session([None, llm_row])

        with patch("src.services.embeddings.factory.get_settings", return_value=mock_settings):
            with pytest.raises(ValueError, match="custom endpoint"):
                await get_embedding_config(session)

    @pytest.mark.asyncio
    async def test_llm_fallback_with_no_endpoint_returns_none(
        self, mock_settings, fernet_instance
    ):
        # LLM row exists but has no endpoint — config.endpoint must be None
        # so the embedding client uses the OpenAI default.
        encrypted_key = fernet_instance.encrypt(b"sk-openai").decode()
        llm_row = _system_config_row(
            LLM_CONFIG_CATEGORY,
            LLM_CONFIG_KEY,
            {
                "provider": "openai",
                "model": "gpt-4o",
                "encrypted_api_key": encrypted_key,
            },
        )
        session = _make_session([None, llm_row])

        with patch("src.services.embeddings.factory.get_settings", return_value=mock_settings):
            config = await get_embedding_config(session)

        assert config.endpoint is None

    @pytest.mark.asyncio
    async def test_dedicated_config_without_endpoint_returns_none(
        self, mock_settings, fernet_instance
    ):
        # Existing rows pre-migration won't have an `endpoint` key. They must
        # surface as None (so the client uses the OpenAI default).
        encrypted_key = fernet_instance.encrypt(b"sk-existing").decode()
        embedding_row = _system_config_row(
            EMBEDDING_CONFIG_CATEGORY,
            EMBEDDING_CONFIG_KEY,
            {
                "model": "text-embedding-3-small",
                "dimensions": 1536,
                "encrypted_api_key": encrypted_key,
            },
        )
        session = _make_session([embedding_row])

        with patch("src.services.embeddings.factory.get_settings", return_value=mock_settings):
            config = await get_embedding_config(session)

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
