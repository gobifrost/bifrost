"""
AI Usage contract models for Bifrost.

Pydantic models for AI usage tracking and model pricing.
"""

import datetime as dt
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer


# ==================== AI MODEL PRICING MODELS ====================


class AIModelPricingBase(BaseModel):
    """Base model for AI model pricing."""

    provider: str = Field(..., max_length=50, description="AI provider (e.g., openai, anthropic)")
    model: str = Field(..., max_length=100, description="Model identifier")
    input_price_per_million: Decimal = Field(..., description="Cost per million input tokens")
    output_price_per_million: Decimal = Field(..., description="Cost per million output tokens")
    cache_read_price_per_million: Decimal | None = Field(
        default=None, description="Cost per million cached input tokens read"
    )
    cache_write_price_per_million: Decimal | None = Field(
        default=None, description="Cost per million cached input tokens written"
    )


class AIModelPricingCreate(AIModelPricingBase):
    """Request model for creating AI model pricing."""

    effective_date: date | None = Field(default=None, description="Date pricing takes effect")


class AIModelPricingUpdate(BaseModel):
    """Request model for updating AI model pricing."""

    input_price_per_million: Decimal | None = Field(
        default=None, description="Cost per million input tokens"
    )
    output_price_per_million: Decimal | None = Field(
        default=None, description="Cost per million output tokens"
    )
    cache_read_price_per_million: Decimal | None = Field(
        default=None, description="Cost per million cached input tokens read"
    )
    cache_write_price_per_million: Decimal | None = Field(
        default=None, description="Cost per million cached input tokens written"
    )
    effective_date: date | None = Field(default=None, description="Date pricing takes effect")


class AIModelPricingPublic(AIModelPricingBase):
    """AI model pricing for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    effective_date: date
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()

    @field_serializer("effective_date")
    def serialize_date(self, d: date) -> str:
        return d.isoformat()

    @field_serializer(
        "input_price_per_million",
        "output_price_per_million",
        "cache_read_price_per_million",
        "cache_write_price_per_million",
    )
    def serialize_decimal(self, d: Decimal | None) -> str | None:
        return str(d) if d is not None else None


# ==================== AI USAGE MODELS ====================


class AIUsageBase(BaseModel):
    """Base model for AI usage."""

    provider: str = Field(..., max_length=50, description="AI provider")
    model: str = Field(..., max_length=100, description="Model identifier")
    input_tokens: int = Field(..., ge=0, description="Number of input tokens")
    output_tokens: int = Field(..., ge=0, description="Number of output tokens")
    cache_read_tokens: int = Field(
        default=0, ge=0, description="Input tokens served from provider cache"
    )
    cache_write_tokens: int = Field(
        default=0, ge=0, description="Input tokens written to provider cache"
    )
    provider_cost: Decimal | None = Field(
        default=None, description="Exact cost reported by the provider"
    )
    cost: Decimal | None = Field(default=None, description="Calculated cost in USD")
    duration_ms: int | None = Field(default=None, ge=0, description="Request duration in milliseconds")


class AIUsagePublic(AIUsageBase):
    """AI usage record for API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    execution_id: UUID | None = None
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    timestamp: datetime
    sequence: int
    organization_id: UUID | None = None
    user_id: UUID | None = None

    @field_serializer("timestamp")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()

    @field_serializer("cost", "provider_cost")
    def serialize_cost(self, d: Decimal | None) -> str | None:
        return str(d) if d is not None else None


class AIUsageTotals(BaseModel):
    """Aggregated AI usage totals."""

    total_input_tokens: int = Field(default=0, description="Total input tokens")
    total_output_tokens: int = Field(default=0, description="Total output tokens")
    total_cache_read_tokens: int = Field(default=0, description="Total cached input tokens read")
    total_cache_write_tokens: int = Field(default=0, description="Total cached input tokens written")
    total_provider_cost: Decimal = Field(default=Decimal("0"), description="Total provider-reported cost in USD")
    total_cost: Decimal = Field(default=Decimal("0"), description="Total cost in USD")
    total_duration_ms: int = Field(default=0, description="Total duration in milliseconds")
    call_count: int = Field(default=0, description="Number of AI calls")

    @field_serializer("total_cost", "total_provider_cost")
    def serialize_cost(self, d: Decimal) -> str:
        return str(d)


class AIUsageByModel(BaseModel):
    """AI usage breakdown by model."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    provider_cost: Decimal = Decimal("0")
    cost: Decimal
    call_count: int

    @field_serializer("cost")
    def serialize_cost(self, d: Decimal) -> str:
        return str(d)


class AIUsageSummaryResponse(BaseModel):
    """Summary response for AI usage."""

    totals: AIUsageTotals
    by_model: list[AIUsageByModel] = Field(default_factory=list)
    usage: list[AIUsagePublic] = Field(default_factory=list)


# ==================== USAGE REPORTS MODELS ====================


class UsageReportSummary(BaseModel):
    """Summary for usage reports."""

    total_ai_cost: Decimal = Field(default=Decimal("0"), description="Total AI cost in USD")
    total_input_tokens: int = Field(default=0, description="Total input tokens")
    total_output_tokens: int = Field(default=0, description="Total output tokens")
    total_ai_calls: int = Field(default=0, description="Total number of AI API calls")
    total_cpu_seconds: float = Field(default=0.0, description="Total CPU time in seconds")
    peak_memory_bytes: int = Field(default=0, description="Peak memory usage in bytes")

    @field_serializer("total_ai_cost")
    def serialize_cost(self, d: Decimal) -> str:
        return str(d)


class UsageTrend(BaseModel):
    """Usage trend data point."""

    date: date
    ai_cost: Decimal = Field(default=Decimal("0"))
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)

    @field_serializer("date")
    def serialize_date(self, d: dt.date) -> str:
        return d.isoformat()

    @field_serializer("ai_cost")
    def serialize_cost(self, d: Decimal) -> str:
        return str(d)


class WorkflowUsage(BaseModel):
    """AI usage by workflow."""

    workflow_name: str
    execution_count: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    ai_cost: Decimal = Field(default=Decimal("0"))
    cpu_seconds: float = Field(default=0.0)
    memory_bytes: int = Field(default=0)

    @field_serializer("ai_cost")
    def serialize_cost(self, d: Decimal) -> str:
        return str(d)


class ConversationUsage(BaseModel):
    """AI usage by conversation."""

    conversation_id: str
    conversation_title: str | None = None
    message_count: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    ai_cost: Decimal = Field(default=Decimal("0"))

    @field_serializer("ai_cost")
    def serialize_cost(self, d: Decimal) -> str:
        return str(d)


class OrganizationUsage(BaseModel):
    """AI usage by organization."""

    organization_id: str
    organization_name: str
    execution_count: int = Field(default=0)
    conversation_count: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    ai_cost: Decimal = Field(default=Decimal("0"))

    @field_serializer("ai_cost")
    def serialize_cost(self, d: Decimal) -> str:
        return str(d)


class AgentUsage(BaseModel):
    """AI usage by agent."""

    agent_name: str
    run_count: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    ai_cost: Decimal = Field(default=Decimal("0"))

    @field_serializer("ai_cost")
    def serialize_cost(self, d: Decimal) -> str:
        return str(d)


class KnowledgeStorageUsage(BaseModel):
    """Knowledge storage usage by organization and namespace."""

    organization_id: str | None = None
    organization_name: str
    namespace: str
    document_count: int = Field(default=0)
    size_bytes: int = Field(default=0)
    size_mb: float = Field(default=0.0)


class KnowledgeStorageTrend(BaseModel):
    """Daily knowledge storage trend data point."""

    date: date
    total_documents: int = Field(default=0)
    total_size_bytes: int = Field(default=0)
    total_size_mb: float = Field(default=0.0)

    @field_serializer("date")
    def serialize_trend_date(self, d: dt.date) -> str:
        return d.isoformat()


class UsageReportResponse(BaseModel):
    """Complete usage report response."""

    summary: UsageReportSummary
    trends: list[UsageTrend] = Field(default_factory=list)
    by_workflow: list[WorkflowUsage] = Field(default_factory=list)
    by_conversation: list[ConversationUsage] = Field(default_factory=list)
    by_agent: list[AgentUsage] = Field(default_factory=list)
    by_organization: list[OrganizationUsage] = Field(default_factory=list)
    knowledge_storage: list[KnowledgeStorageUsage] = Field(default_factory=list)
    knowledge_storage_trends: list[KnowledgeStorageTrend] = Field(default_factory=list)
    knowledge_storage_as_of: date | None = None

    @field_serializer("knowledge_storage_as_of")
    def serialize_storage_date(self, d: dt.date | None) -> str | None:
        return d.isoformat() if d else None


# ==================== PRICING LIST MODELS ====================


class AIModelPricingListItem(AIModelPricingPublic):
    """Pricing list item with usage indicator."""

    is_used: bool = Field(default=False, description="Whether this model has been used")


class AIModelPricingListResponse(BaseModel):
    """Response for listing model pricing."""

    pricing: list[AIModelPricingListItem] = Field(default_factory=list)
    models_without_pricing: list[str] = Field(
        default_factory=list,
        description="Models that have been used but don't have pricing configured"
    )
