"""AI provider connection and reusable model profile ORM models."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.agents import Agent


class AIProviderConnection(Base):
    """Named credentials and endpoint for an AI provider."""

    __tablename__ = "ai_provider_connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    profiles: Mapped[list["AIModelProfile"]] = relationship(back_populates="connection", lazy="selectin")
    embedding_config: Mapped["AIEmbeddingConfig | None"] = relationship(
        back_populates="connection",
        lazy="selectin",
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN ('openai', 'anthropic', 'google', 'openrouter', 'openai_compatible')",
            name="ck_ai_provider_connections_provider",
        ),
        Index("uq_ai_provider_connections_name_ci", text("lower(name)"), unique=True),
    )


class AIModelProfile(Base):
    """Reusable model selection backed by a provider connection."""

    __tablename__ = "ai_model_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_provider_connections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    capabilities: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    enabled_for_chat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    connection: Mapped[AIProviderConnection] = relationship(back_populates="profiles", lazy="joined")
    assignments: Mapped[list["AIModelAssignment"]] = relationship(back_populates="profile", lazy="selectin")
    agents: Mapped[list["Agent"]] = relationship(back_populates="llm_profile", lazy="selectin")

    __table_args__ = (
        Index("uq_ai_model_profiles_name_ci", text("lower(name)"), unique=True),
        Index("ix_ai_model_profiles_connection_id", "connection_id"),
        Index("ix_ai_model_profiles_enabled_for_chat", "enabled_for_chat"),
    )


class AIModelAssignment(Base):
    """Global model profile assignment for a runtime use case."""

    __tablename__ = "ai_model_assignments"

    assignment_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_model_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    profile: Mapped[AIModelProfile] = relationship(back_populates="assignments", lazy="joined")

    __table_args__ = (
        CheckConstraint(
            "assignment_key IN ('primary', 'summarization', 'tuning', 'image_generation', 'video_generation', 'chat_default')",
            name="ck_ai_model_assignments_key",
        ),
        Index("ix_ai_model_assignments_profile_id", "profile_id"),
    )


class AIEmbeddingConfig(Base):
    """Singleton embedding model selection backed by a provider connection."""

    __tablename__ = "ai_embedding_configs"

    key: Mapped[str] = mapped_column(String(50), primary_key=True, default="default")
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_provider_connections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=1536, server_default="1536")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    connection: Mapped[AIProviderConnection] = relationship(back_populates="embedding_config", lazy="joined")

    __table_args__ = (
        CheckConstraint("key = 'default'", name="ck_ai_embedding_configs_singleton"),
        CheckConstraint("dimensions > 0", name="ck_ai_embedding_configs_dimensions_positive"),
        Index("ix_ai_embedding_configs_connection_id", "connection_id"),
    )
