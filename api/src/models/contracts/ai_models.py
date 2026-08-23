"""Contracts for reusable AI provider connections and model profiles."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.models.contracts.artifacts import ModelCapabilities
from src.models.contracts.llm import LLMModelInfo

AIProviderKind = Literal["openai", "anthropic", "google", "openrouter", "openai_compatible"]
AIModelAssignmentKey = Literal[
    "primary",
    "summarization",
    "tuning",
    "image_generation",
    "video_generation",
    "chat_default",
]


class AIProviderConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider: AIProviderKind
    api_key: str = Field(..., min_length=1)
    endpoint: str | None = Field(default=None, max_length=500)


class AIProviderConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    provider: AIProviderKind | None = None
    api_key: str | None = Field(default=None)
    endpoint: str | None = Field(default=None, max_length=500)


class AIProviderConnectionResponse(BaseModel):
    id: UUID
    name: str
    provider: AIProviderKind
    endpoint: str | None = None
    api_key_set: bool
    profile_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AIProviderConnectionSummary(BaseModel):
    id: UUID
    name: str
    provider: AIProviderKind
    endpoint: str | None = None


class AIModelProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    connection_id: UUID
    model: str = Field(..., min_length=1, max_length=200)
    capabilities: ModelCapabilities | None = None
    enabled_for_chat: bool = False


class AIModelProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    connection_id: UUID | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    capabilities: ModelCapabilities | None = None
    enabled_for_chat: bool | None = None


class AIModelProfileMergeRequest(BaseModel):
    profile_ids: list[UUID] = Field(..., min_length=2)
    target_profile_id: UUID

    @model_validator(mode="after")
    def validate_profile_selection(self) -> "AIModelProfileMergeRequest":
        if len(set(self.profile_ids)) != len(self.profile_ids):
            raise ValueError("Profile selection must not contain duplicates")
        if self.target_profile_id not in self.profile_ids:
            raise ValueError("Target profile must be included in the profile selection")
        return self


class AIModelProfileResponse(BaseModel):
    id: UUID
    name: str
    connection_id: UUID
    model: str
    capabilities: ModelCapabilities | None = None
    enabled_for_chat: bool
    connection: AIProviderConnectionSummary
    assignment_keys: list[AIModelAssignmentKey] = Field(default_factory=list)
    referenced_agent_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AIModelProfileMergeResponse(BaseModel):
    profile: AIModelProfileResponse
    merged_profile_ids: list[UUID]
    reassigned_agent_count: int
    reassigned_assignment_keys: list[AIModelAssignmentKey]


class AIModelAssignmentUpdate(BaseModel):
    profile_id: UUID


class AIModelAssignmentResponse(BaseModel):
    assignment_key: AIModelAssignmentKey
    profile_id: UUID
    profile: AIModelProfileResponse
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AIConnectionTestResponse(BaseModel):
    success: bool
    message: str
    models: list[LLMModelInfo] | None = None


class AIModelsResponse(BaseModel):
    provider: AIProviderKind
    models: list[LLMModelInfo]
