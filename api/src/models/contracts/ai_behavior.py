"""Contracts for profile-independent AI behavior settings."""

from typing import Literal

from pydantic import BaseModel, Field


class AIBehaviorResponse(BaseModel):
    default_system_prompt: str | None = None
    agent_execution_mode: Literal["delegated", "automatic"]


class AIBehaviorUpdate(BaseModel):
    default_system_prompt: str | None = Field(default=None, max_length=50_000)
    # A schema default makes this input required in generated TypeScript.
    # Omission must stay available for independent prompt-only updates.
    agent_execution_mode: Literal["delegated", "automatic"] = Field(
        default_factory=lambda: "delegated",
        description="Omit to retain the current platform execution mode.",
    )
