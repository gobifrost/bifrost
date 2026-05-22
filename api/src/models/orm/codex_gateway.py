"""ORM models for the Bifrost Codex Gateway."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.users import User


class CodexGatewayUpstreamAccount(Base):
    """Per-user upstream ChatGPT/Codex OAuth account.

    These rows carry encrypted upstream tokens and must never be treated as
    global/shared credentials. Ambiguous or revoked mappings should fail closed
    at the repository/service layer.
    """

    __tablename__ = "codex_gateway_upstream_accounts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(64), default="chatgpt_codex", nullable=False
    )
    upstream_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    upstream_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    upstream_workspace_id: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False, server_default="[]"
    )
    last_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    user: Mapped["User"] = relationship(lazy="select")

    __table_args__ = (
        Index(
            "ix_codex_gateway_upstream_accounts_user_provider",
            "user_id",
            "provider",
            "revoked_at",
        ),
        Index(
            "ix_codex_gateway_upstream_accounts_subject",
            "provider",
            "upstream_subject",
        ),
    )


class CodexGatewayKey(Base):
    """Downstream gateway key mapped to one Bifrost user and optional project."""

    __tablename__ = "codex_gateway_keys"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID | None] = mapped_column(nullable=True)
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_models: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False, server_default="[]"
    )
    denied_models: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False, server_default="[]"
    )
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(lazy="select")

    __table_args__ = (
        Index("ix_codex_gateway_keys_user_status", "user_id", "status"),
        Index("ix_codex_gateway_keys_project_status", "project_id", "status"),
    )


class CodexGatewayRequestLog(Base):
    """Metadata-only gateway request log.

    Raw prompts and responses stay empty by default. They are only populated by
    callers that explicitly enable sensitive payload capture for a project or
    policy path.
    """

    __tablename__ = "codex_gateway_request_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(nullable=True)
    gateway_key_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("codex_gateway_keys.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    oauth_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "codex_gateway_upstream_accounts.id",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        nullable=True,
    )
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    streaming: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    denied_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    client_user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_metadata: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False, server_default="{}"
    )
    captured_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    user: Mapped["User | None"] = relationship(lazy="select")
    gateway_key: Mapped["CodexGatewayKey | None"] = relationship(lazy="select")
    oauth_account: Mapped["CodexGatewayUpstreamAccount | None"] = relationship(
        lazy="select"
    )

    __table_args__ = (
        Index("ix_codex_gateway_request_logs_user_time", "user_id", "created_at"),
        Index(
            "ix_codex_gateway_request_logs_project_time",
            "project_id",
            "created_at",
        ),
        Index(
            "ix_codex_gateway_request_logs_decision_time",
            "policy_decision",
            "created_at",
        ),
    )
