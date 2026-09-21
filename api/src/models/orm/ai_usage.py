"""
AI Usage and Model Pricing ORM models.

Tracks AI provider usage across workflow executions and chat conversations.
"""

from datetime import date, datetime, timezone
from uuid import uuid4
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.agents import Conversation, Message
    from src.models.orm.executions import Execution
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User
    from src.models.orm.platform_jobs import PlatformJob


class AIModelPricing(Base):
    """Pricing configuration for AI models."""

    __tablename__ = "ai_model_pricing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_price_per_million: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    output_price_per_million: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    cache_read_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), default=None
    )
    cache_write_price_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), default=None
    )
    effective_date: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=text("CURRENT_DATE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("provider", "model", name="uq_ai_model_pricing_provider_model"),
    )


class AIUsageAttempt(Base):
    """Durable accounting intent for quality LLM calls.

    This records call accounting provenance only. It is not a job lifecycle,
    retry queue, progress tracker, or provider request log.
    """

    __tablename__ = "ai_usage_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    quality_operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_operation_id: Mapped[UUID] = mapped_column(nullable=False)
    quality_operation_item_id: Mapped[str | None] = mapped_column(String(255), default=None)
    usage_purpose: Mapped[str] = mapped_column(String(64), nullable=False)

    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), default=None
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    platform_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("platform_jobs.id", ondelete="SET NULL"), default=None
    )

    profile_id: Mapped[UUID | None] = mapped_column(default=None)
    profile_name: Mapped[str | None] = mapped_column(String(255), default=None)
    profile_fingerprint: Mapped[str | None] = mapped_column(String(128), default=None)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)

    state: Mapped[str] = mapped_column(String(20), nullable=False, server_default="started")
    unobserved_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    organization: Mapped["Organization | None"] = relationship()
    user: Mapped["User | None"] = relationship()
    platform_job: Mapped["PlatformJob | None"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "state IN ('started', 'observed', 'unobserved')",
            name="ai_usage_attempt_state_check",
        ),
        CheckConstraint(
            "(state = 'unobserved' AND unobserved_reason IS NOT NULL) "
            "OR (state != 'unobserved' AND unobserved_reason IS NULL)",
            name="ai_usage_attempt_unobserved_reason_state_check",
        ),
        Index("ix_ai_usage_attempt_operation", "quality_operation_type", "quality_operation_id"),
        Index("ix_ai_usage_attempt_platform_job", "platform_job_id"),
    )


# Identity entity — AI cost/usage telemetry, not name-cascade resolved.
# See api/src/repositories/README.md.
class AIUsage(Base):
    """AI usage tracking per execution or conversation."""

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Context - at least one must be set
    execution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), default=None
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), default=None
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), default=None
    )
    message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), default=None
    )

    # Quality-operation context for non-production evaluation/review overhead.
    quality_operation_type: Mapped[str | None] = mapped_column(String(64), default=None)
    quality_operation_id: Mapped[UUID | None] = mapped_column(default=None)
    quality_operation_item_id: Mapped[str | None] = mapped_column(String(255), default=None)
    usage_purpose: Mapped[str | None] = mapped_column(String(64), default=None)
    profile_id: Mapped[UUID | None] = mapped_column(default=None)
    profile_name: Mapped[str | None] = mapped_column(String(255), default=None)
    profile_fingerprint: Mapped[str | None] = mapped_column(String(128), default=None)
    platform_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("platform_jobs.id", ondelete="SET NULL"), default=None
    )
    usage_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_usage_attempts.id"), default=None, unique=True
    )
    # Usage details
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    provider_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 8), default=None
    )
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    # Metadata
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Optional organization/user tracking
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), default=None
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    # Relationships
    execution: Mapped["Execution | None"] = relationship(back_populates="ai_usages")
    conversation: Mapped["Conversation | None"] = relationship(back_populates="ai_usages")
    agent_run: Mapped["AgentRun | None"] = relationship(back_populates="ai_usages")
    message: Mapped["Message | None"] = relationship()
    organization: Mapped["Organization | None"] = relationship()
    user: Mapped["User | None"] = relationship()
    platform_job: Mapped["PlatformJob | None"] = relationship()
    usage_attempt: Mapped["AIUsageAttempt | None"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "execution_id IS NOT NULL OR conversation_id IS NOT NULL OR agent_run_id IS NOT NULL "
            "OR (quality_operation_type IS NOT NULL AND quality_operation_id IS NOT NULL)",
            name="ai_usage_context_check",
        ),
        CheckConstraint(
            "(quality_operation_type IS NULL AND quality_operation_id IS NULL) "
            "OR (quality_operation_type IS NOT NULL AND quality_operation_id IS NOT NULL)",
            name="ai_usage_quality_operation_pair_check",
        ),
        CheckConstraint(
            "quality_operation_type IS NULL OR usage_purpose IS NOT NULL",
            name="ai_usage_quality_requires_purpose_check",
        ),
        CheckConstraint(
            "usage_purpose IS NULL OR "
            "(quality_operation_type IS NOT NULL AND quality_operation_id IS NOT NULL)",
            name="ai_usage_purpose_requires_quality_context_check",
        ),
        CheckConstraint(
            "usage_attempt_id IS NULL OR "
            "(quality_operation_type IS NOT NULL AND quality_operation_id IS NOT NULL "
            "AND usage_purpose IS NOT NULL)",
            name="ai_usage_attempt_requires_quality_context_check",
        ),
        CheckConstraint(
            "quality_operation_type IS NULL OR "
            "(execution_id IS NULL AND conversation_id IS NULL AND agent_run_id IS NULL)",
            name="ai_usage_quality_no_runtime_context_check",
        ),
        Index(
            "ix_ai_usage_execution",
            "execution_id",
            postgresql_where=text("execution_id IS NOT NULL"),
        ),
        Index(
            "ix_ai_usage_conversation",
            "conversation_id",
            postgresql_where=text("conversation_id IS NOT NULL"),
        ),
        Index(
            "ix_ai_usage_agent_run",
            "agent_run_id",
            postgresql_where=text("agent_run_id IS NOT NULL"),
        ),
        Index("ix_ai_usage_org", "organization_id"),
        Index("ix_ai_usage_timestamp", "timestamp"),
        Index(
            "ix_ai_usage_quality_operation",
            "quality_operation_type",
            "quality_operation_id",
            "timestamp",
            postgresql_where=text("quality_operation_type IS NOT NULL"),
        ),
        Index(
            "ix_ai_usage_quality_purpose",
            "organization_id",
            "usage_purpose",
            "provider",
            "model",
            "timestamp",
            postgresql_where=text("usage_purpose IS NOT NULL"),
        ),
        Index(
            "ix_ai_usage_platform_job",
            "platform_job_id",
            postgresql_where=text("platform_job_id IS NOT NULL"),
        ),
    )
