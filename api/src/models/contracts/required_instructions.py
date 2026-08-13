"""Contracts for editable and resolved Bifrost instructions."""

from pydantic import BaseModel, Field


class RequiredInstructionsSettings(BaseModel):
    instructions: str = Field(default="", max_length=50_000)


class RequiredInstructionsResponse(BaseModel):
    instructions: list[str] = Field(default_factory=list)
