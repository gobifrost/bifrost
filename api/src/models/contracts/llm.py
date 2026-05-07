"""
LLM Configuration Pydantic Models

Request/response models for LLM admin endpoints.
"""

from typing import Literal

from pydantic import BaseModel, Field


class LLMConfigResponse(BaseModel):
    """LLM configuration response (API key is never returned)."""

    provider: Literal["openai", "anthropic"]
    model: str
    endpoint: str | None = None
    max_tokens: int = 16384
    default_system_prompt: str | None = None
    summarization_model: str | None = None
    tuning_model: str | None = None
    is_configured: bool = True
    api_key_set: bool = False


class LLMConfigRequest(BaseModel):
    """Request to set LLM configuration."""

    provider: Literal["openai", "anthropic"] = Field(
        ...,
        description="LLM provider type",
    )
    model: str = Field(
        ...,
        min_length=1,
        description="Model identifier (e.g., 'gpt-4o', 'claude-sonnet-4-20250514')",
    )
    api_key: str | None = Field(
        None,
        description="API key for the provider. Omit to preserve existing key.",
    )
    endpoint: str | None = Field(
        None,
        description="Custom API endpoint URL (e.g., for Azure OpenAI, Ollama, or other compatible providers)",
    )
    max_tokens: int = Field(
        16384,
        ge=1,
        le=128000,
        description="Maximum tokens for completion",
    )
    default_system_prompt: str | None = Field(
        None,
        description="Default system prompt for agentless chat",
    )
    summarization_model: str | None = Field(
        default=None,
        description="Model override for post-run summarization. Falls back to primary model if unset.",
    )
    tuning_model: str | None = Field(
        default=None,
        description="Model override for tuning chat + dry-run. Falls back to primary model if unset.",
    )


class LLMTestRequest(BaseModel):
    """Request to test LLM configuration before saving."""

    provider: Literal["openai", "anthropic"] = Field(
        ...,
        description="LLM provider type",
    )
    model: str | None = Field(
        None,
        min_length=1,
        description="Optional model identifier. Connection tests list provider models without probing a guessed default.",
    )
    api_key: str | None = Field(
        None,
        min_length=1,
        description="API key to test. Omit to test current form settings with the saved key.",
    )
    endpoint: str | None = Field(
        None,
        description="Custom API endpoint URL",
    )


class LLMModelInfo(BaseModel):
    """Model information with both ID and display name."""

    id: str
    display_name: str


class LLMTestResponse(BaseModel):
    """Response from testing LLM connection."""

    success: bool
    message: str
    models: list[LLMModelInfo] | None = None


class LLMModelsResponse(BaseModel):
    """Response listing available models."""

    models: list[LLMModelInfo]
    provider: str


# =============================================================================
# Embedding Configuration
# =============================================================================


class EmbeddingConfigResponse(BaseModel):
    """Embedding configuration response (API key is never returned)."""

    model: str = "text-embedding-3-small"
    dimensions: int = 1536  # Last-known vector size; informational only.
    endpoint: str | None = None  # Resolved endpoint (dedicated, inherited, or null = OpenAI default).
    is_configured: bool = True
    api_key_set: bool = False
    uses_llm_key: bool = False  # True if falling back to LLM provider's key.


class EmbeddingConfigRequest(BaseModel):
    """Request to set dedicated embedding configuration."""

    api_key: str | None = Field(
        None,
        description="API key for embeddings. Omit to preserve existing key.",
    )
    model: str = Field(
        "text-embedding-3-small",
        description="Embedding model identifier",
    )
    endpoint: str | None = Field(
        None,
        description="Custom OpenAI-compatible endpoint URL. Null/empty means OpenAI default.",
    )
    confirm_reindex: bool = Field(
        default=False,
        description=(
            "When the new model's vector dimension differs from the saved one and "
            "knowledge_store has existing rows, the first POST returns "
            "needs_reindex_confirmation. Re-POST with this flag set to true to "
            "persist the new config and trigger a reindex."
        ),
    )


class EmbeddingTestRequest(BaseModel):
    """Request to test an embedding configuration before saving."""

    api_key: str | None = Field(
        None,
        description="API key to test. Omit to use the saved key.",
    )
    model: str = Field(
        "text-embedding-3-small",
        description="Embedding model identifier",
    )
    endpoint: str | None = Field(
        None,
        description="Endpoint URL to test against. Null/empty means OpenAI default.",
    )


class EmbeddingTestResponse(BaseModel):
    """Response from testing embedding configuration."""

    success: bool
    message: str
    dimensions: int | None = None
    models: list[str] | None = None  # Embedding-capable model ids when endpoint exposes them.


class EmbeddingReindexResponse(BaseModel):
    """Response from triggering an on-demand embedding reindex."""

    notification_id: str = Field(
        ...,
        description=(
            "Notification ID — subscribe via WebSocket on `notification:{user_id}` "
            "to track progress, cancel via DELETE /api/notifications/{id}."
        ),
    )
    row_count: int = Field(
        ...,
        description="Number of knowledge_store rows that will be re-embedded.",
    )


class EmbeddingConfigSaveResponse(BaseModel):
    """
    Response from POST /embedding-config.

    Two shapes:
    - Save persisted: `saved=True`, `config` populated, `notification_id` set if
      a reindex was kicked off (dim change confirmed).
    - Confirmation needed: `saved=False`, `needs_reindex_confirmation=True`,
      and the dim-change details populated. Re-POST with `confirm_reindex: true`
      to proceed.
    """

    saved: bool
    config: EmbeddingConfigResponse | None = None
    notification_id: str | None = Field(
        default=None,
        description="Set when a reindex was triggered alongside the save.",
    )
    needs_reindex_confirmation: bool = False
    reason: str | None = Field(
        default=None,
        description="Why confirmation is required (e.g. 'dim_change').",
    )
    old_dim: int | None = None
    new_dim: int | None = None
    old_model: str | None = None
    new_model: str | None = None
    row_count: int | None = Field(
        default=None,
        description="Rows that would be re-embedded if confirmed.",
    )

