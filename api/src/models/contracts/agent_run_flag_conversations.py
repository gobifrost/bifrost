"""Flag conversation message contracts.

Polymorphic turn types stored inside the JSONB ``messages`` array on
``AgentRunFlagConversation``. Each turn carries a ``kind`` discriminator and
its own payload shape:

- ``UserTurn`` — reviewer free-text
- ``AssistantTurn`` — assistant free-text
- ``ProposalTurn`` — assistant proposal with a structured prompt diff
- ``DryRunTurn`` — assistant dry-run preview (before / after / predicted)
"""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserTurn(BaseModel):
    kind: Literal["user"] = "user"
    content: str
    at: datetime = Field(default_factory=_utcnow)


class AssistantTurn(BaseModel):
    kind: Literal["assistant"] = "assistant"
    content: str
    at: datetime = Field(default_factory=_utcnow)


class DiffOperation(BaseModel):
    op: Literal["add", "keep", "remove"]
    text: str


class ProposalTurn(BaseModel):
    kind: Literal["proposal"] = "proposal"
    summary: str
    diff: list[DiffOperation]
    at: datetime = Field(default_factory=_utcnow)


class DryRunTurn(BaseModel):
    kind: Literal["dryrun"] = "dryrun"
    before: str
    after: str
    predicted: Literal["up", "down"]
    evaluation_failed: bool = False
    at: datetime = Field(default_factory=_utcnow)


FlagConversationMessage = UserTurn | AssistantTurn | ProposalTurn | DryRunTurn


class FlagConversationResponse(BaseModel):
    id: UUID
    run_id: UUID
    messages: list[UserTurn | AssistantTurn | ProposalTurn | DryRunTurn]
    created_at: datetime
    last_updated_at: datetime


class SendFlagMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
