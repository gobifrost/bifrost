"""
OpenAI Embedding Client

Uses OpenAI's embedding API for generating text embeddings.
Default model: text-embedding-3-small (1536 dimensions)
"""

import logging

from openai import AsyncOpenAI

from src.services.embeddings.base import BaseEmbeddingClient, EmbeddingConfig

logger = logging.getLogger(__name__)


class EmptyEmbeddingResponseError(Exception):
    """
    Raised when the upstream embeddings endpoint returns 200 with an
    unusable body (data missing, None, or empty).

    OpenAI-compatible providers like OpenRouter sometimes return a 200 with
    no `data` field for over-cap batches, token-cap hits, or soft-throttled
    requests. The OpenAI SDK silently parses these into a response where
    `data is None`, which then crashes downstream consumers with a generic
    NoneType error. Surface a typed error with provider/model/batch context
    so logs say *what* failed.
    """

    def __init__(self, *, model: str, endpoint: str | None, batch_size: int):
        self.model = model
        self.endpoint = endpoint
        self.batch_size = batch_size
        super().__init__(
            f"Embeddings endpoint returned no data "
            f"(model={model!r}, endpoint={endpoint or 'default'!r}, "
            f"batch_size={batch_size})"
        )


class OpenAIEmbeddingClient(BaseEmbeddingClient):
    """
    OpenAI embedding client.

    Uses the OpenAI API to generate text embeddings.
    Supports batch embedding for efficiency.
    """

    def __init__(self, config: EmbeddingConfig):
        super().__init__(config)
        self._client = AsyncOpenAI(api_key=config.api_key, base_url=config.endpoint or None)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Uses OpenAI's batch embedding endpoint for efficiency. On an empty
        200 response (see ``EmptyEmbeddingResponseError``), recursively
        halve the batch and retry — some OpenAI-compatible providers
        (OpenRouter pass-through) silently 200 with no data when the input
        array exceeds an undocumented per-request cap. Halving down to N=1
        recovers the working slice of the batch; if N=1 still fails the
        error propagates so the caller can fail loudly.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors, in the same order as ``texts``.
        """
        if not texts:
            return []

        # OpenAI has a max batch size of 2048
        # For larger batches, we'd need to chunk, but 2048 is plenty for most use cases
        if len(texts) > 2048:
            logger.warning(f"Batch size {len(texts)} exceeds 2048, truncating")
            texts = texts[:2048]

        try:
            # Force `encoding_format="float"` rather than letting the SDK
            # default to base64. Google AI Studio (and OpenRouter's pass-through
            # to it) explicitly rejects base64 with a 200-shaped error body
            # the SDK then silently turns into "No embedding data received".
            # Plain floats work everywhere.
            response = await self._client.embeddings.create(
                input=texts,
                model=self.config.model,
                encoding_format="float",
            )
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise

        if not response.data:
            # Empty 200 — typically a provider-side per-request cap. Halve
            # and retry; bottom out at N=1 with a typed error.
            if len(texts) == 1:
                raise EmptyEmbeddingResponseError(
                    model=self.config.model,
                    endpoint=self.config.endpoint,
                    batch_size=1,
                )
            mid = len(texts) // 2
            logger.warning(
                f"Empty 200 from embeddings endpoint at batch_size={len(texts)}; "
                f"splitting into {mid} + {len(texts) - mid} and retrying"
            )
            left = await self.embed(texts[:mid])
            right = await self.embed(texts[mid:])
            return left + right

        # Sort by index to ensure order matches input
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]

    async def embed_single(self, text: str) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text string to embed

        Returns:
            Embedding vector as a list of floats
        """
        embeddings = await self.embed([text])
        return embeddings[0]
