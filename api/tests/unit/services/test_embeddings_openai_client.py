"""
Unit tests for OpenAIEmbeddingClient.embed() — specifically the empty-200
recovery path that backs issue #198.

OpenRouter (and other OpenAI-compatible providers) sometimes return a 200
with `data=None` for over-cap batches; the SDK silently parses that into a
response where the consumer iterates None. We turn that into a typed error
plus halve-and-retry so a working sub-batch can still complete.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.embeddings.base import EmbeddingConfig
from src.services.embeddings.openai_client import (
    EmptyEmbeddingResponseError,
    OpenAIEmbeddingClient,
)


def _make_response(items):
    """Build a fake openai response object with a `data` attribute."""
    return SimpleNamespace(data=items)


def _embedding_item(index: int, dim: int = 4):
    """One openai-shaped embedding-item record."""
    return SimpleNamespace(index=index, embedding=[float(index)] * dim)


@pytest.fixture
def client():
    cfg = EmbeddingConfig(
        api_key="sk-test",
        model="text-embedding-3-large",
        endpoint="https://example.invalid/v1",
    )
    with patch(
        "src.services.embeddings.openai_client.AsyncOpenAI"
    ) as async_openai:
        # The SUT only uses _client.embeddings.create — wire that.
        async_openai.return_value = MagicMock()
        async_openai.return_value.embeddings = MagicMock()
        async_openai.return_value.embeddings.create = AsyncMock()
        c = OpenAIEmbeddingClient(cfg)
    return c


@pytest.mark.asyncio
async def test_embed_returns_ordered_vectors_on_happy_path(client):
    """Plain success path — verifies order is preserved by `index`."""
    # Provider returns results out of order; client must sort by index.
    client._client.embeddings.create = AsyncMock(
        return_value=_make_response(
            [_embedding_item(2), _embedding_item(0), _embedding_item(1)]
        )
    )
    out = await client.embed(["a", "b", "c"])
    assert [v[0] for v in out] == [0.0, 1.0, 2.0]


@pytest.mark.asyncio
async def test_embed_raises_typed_error_when_n1_returns_no_data(client):
    """At N=1 there's nothing to halve — propagate the typed error."""
    client._client.embeddings.create = AsyncMock(
        return_value=_make_response(None)
    )
    with pytest.raises(EmptyEmbeddingResponseError) as exc:
        await client.embed(["only one"])
    assert exc.value.batch_size == 1
    assert exc.value.model == "text-embedding-3-large"


@pytest.mark.asyncio
async def test_embed_halves_and_retries_on_empty_200_batch(client):
    """The bug from prod: N=256 returns data=None, but smaller batches succeed.

    We simulate the reproducer by tracking the per-call batch size: any call
    with len(input) > 4 gets data=None; smaller batches get a real response.
    Halve-and-retry should drive the recursion down until each leaf works
    and then concatenate the leaves in order.
    """
    call_log = []

    async def fake_create(*, input, model, encoding_format):
        call_log.append(len(input))
        if len(input) > 4:
            return _make_response(None)
        # Index is per-call (per the OpenAI contract).
        return _make_response(
            [_embedding_item(i) for i in range(len(input))]
        )

    client._client.embeddings.create = AsyncMock(side_effect=fake_create)

    inputs = [f"text-{i}" for i in range(8)]
    out = await client.embed(inputs)

    # We should have gotten 8 vectors back, in order.
    assert len(out) == 8
    # First call had the full batch (8); subsequent halved (4 + 4).
    assert call_log[0] == 8
    assert sorted(call_log[1:]) == [4, 4]


@pytest.mark.asyncio
async def test_embed_halves_recursively_to_n1_then_propagates(client):
    """Provider is broken at every batch size — error propagates from the
    deepest leaf with the typed error, not a generic NoneType crash."""
    client._client.embeddings.create = AsyncMock(
        return_value=_make_response(None)
    )
    with pytest.raises(EmptyEmbeddingResponseError):
        await client.embed(["a", "b", "c", "d"])


@pytest.mark.asyncio
async def test_embed_empty_input_short_circuits(client):
    """No texts → no API call, empty list back."""
    client._client.embeddings.create = AsyncMock()
    out = await client.embed([])
    assert out == []
    client._client.embeddings.create.assert_not_awaited()
