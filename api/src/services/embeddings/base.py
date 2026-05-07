"""
Embedding Service Base Interface

Abstract base class and configuration for embedding providers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


# Default embedding model - 1536 dimensions, good balance of quality and cost
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


@dataclass
class EmbeddingConfig:
    """Configuration for embedding client."""

    api_key: str
    model: str = DEFAULT_EMBEDDING_MODEL
    dimensions: int = EMBEDDING_DIMENSIONS
    endpoint: str | None = None


class BaseEmbeddingClient(ABC):
    """
    Abstract base class for embedding providers.

    Implementations must provide embedding generation for text.
    """

    def __init__(self, config: EmbeddingConfig):
        self.config = config

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors (each is a list of floats)
        """
        ...

    @abstractmethod
    async def embed_single(self, text: str) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text string to embed

        Returns:
            Embedding vector as a list of floats
        """
        ...

    @property
    def model_name(self) -> str:
        """Return the model name."""
        return self.config.model

    @property
    def dimensions(self) -> int:
        """Return the embedding dimensions."""
        return self.config.dimensions
