"""
OpenAI Embedding Client

Uses OpenAI's embedding API for generating text embeddings.
Default model: text-embedding-3-small (1536 dimensions)
"""

import logging

from openai import AsyncOpenAI

from src.services.embeddings.base import BaseEmbeddingClient, EmbeddingConfig

logger = logging.getLogger(__name__)


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

        Uses OpenAI's batch embedding endpoint for efficiency.
        Maximum batch size is 2048 texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors
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

            # Sort by index to ensure order matches input
            sorted_data = sorted(response.data, key=lambda x: x.index)
            return [item.embedding for item in sorted_data]

        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise

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
