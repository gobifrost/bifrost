"""Shared Pydantic models used by API routers and clients."""

from typing import Any

from pydantic import BaseModel, RootModel


class VersionResponse(BaseModel):
    version: str


class CodexGatewayResponsesRequest(RootModel[dict[str, Any]]):
    """OpenAI-compatible Responses API request payload."""
