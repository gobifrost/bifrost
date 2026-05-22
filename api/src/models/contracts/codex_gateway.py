"""
Contracts for the Bifrost Codex Gateway governance layer.

These models intentionally describe Bifrost-side identity and policy context,
not upstream ChatGPT/Codex token material. Upstream OAuth tokens stay in the
vault layer and must never be serialized through these contracts.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


CodexGatewayKeyStatus = Literal["active", "revoked"]


class CodexGatewayKeyContext(BaseModel):
    """Downstream gateway key/session context resolved by Bifrost auth."""

    id: UUID
    user_id: UUID
    project_id: UUID | None = None
    name: str
    allowed_models: list[str] = Field(default_factory=list)
    denied_models: list[str] = Field(default_factory=list)
    daily_limit: int | None = None
    monthly_limit: int | None = None
    status: CodexGatewayKeyStatus = "active"


class CodexGatewayUpstreamAccount(BaseModel):
    """Metadata for a user's connected ChatGPT/Codex identity."""

    id: UUID
    user_id: UUID
    upstream_subject: str
    upstream_email: str | None = None
    upstream_workspace_id: str | None = None
    access_token_expires_at: datetime | None = None
    last_refresh_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class CodexGatewayRequestContext(BaseModel):
    """Metadata extracted from an OpenAI-compatible gateway request."""

    request_id: str
    endpoint: str
    model: str
    streaming: bool = False
    client_type: str | None = None
    source_ip: str | None = None
    client_user_agent: str | None = None
    input_token_count: int | None = None
    output_token_count: int | None = None
    requested_max_output_tokens: int | None = None
    prompt_capture_enabled: bool = False
    response_capture_enabled: bool = False
    sensitive_input_preview: str | None = Field(
        default=None,
        exclude=True,
        repr=False,
        description="Testing hook for proving prompts are not logged by default.",
    )
    sensitive_output_preview: str | None = Field(
        default=None,
        exclude=True,
        repr=False,
        description="Testing hook for proving responses are not logged by default.",
    )


class OpenAICompatibleError(BaseModel):
    """OpenAI-compatible error object returned by gateway denials."""

    message: str
    type: str = "invalid_request_error"
    code: str
    param: str | None = None


class CodexGatewayPolicyDecision(BaseModel):
    """Policy result plus metadata safe to persist in audit/request logs."""

    allowed: bool
    code: str
    message: str
    status_code: int
    audit_metadata: dict[str, Any]
    openai_error: OpenAICompatibleError
