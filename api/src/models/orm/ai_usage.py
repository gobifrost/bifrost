"""
AI Usage and Model Pricing ORM models.

Tracks AI provider usage across workflow executions and chat conversations.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import date, datetime, timezone
from decimal import Decimal
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

    # Usage details
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
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

    __table_args__ = (
        CheckConstraint(
            "execution_id IS NOT NULL OR conversation_id IS NOT NULL OR agent_run_id IS NOT NULL",
            name="ai_usage_context_check",
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
    )
