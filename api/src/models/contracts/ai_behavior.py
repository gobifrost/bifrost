"""Contracts for profile-independent AI behavior settings."""

from pydantic import BaseModel, Field


class AIBehaviorResponse(BaseModel):
    default_system_prompt: str | None = None


class AIBehaviorUpdate(BaseModel):
    default_system_prompt: str | None = Field(default=None, max_length=50_000)
