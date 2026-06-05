"""
Workflow ORM model.

Represents all executable user code (workflows, tools, data providers)
discovered from Python files. Data providers were consolidated into this
table in migration 20260103_000000.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.agents import Agent
    from src.models.orm.organizations import Organization
    from src.models.orm.users import Role
    from src.models.orm.workflow_roles import WorkflowRole


# Execution-resolution entity — access via WorkflowRepository (OrgScopedRepository).
# RBAC checked at execution start.
# See api/src/repositories/README.md.
class Workflow(Base):
    """
    Workflow registry - stores metadata for all executable user code.

    This table stores metadata for all executable user code discovered from
    Python files in the workspace. File changes are synced to this table
    when files are written via the API or git sync.

    Types:
    - workflow: Standard workflows (@workflow decorator)
    - tool: AI agent tools (@tool decorator)
    - data_provider: Data providers for forms/app builder (@data_provider decorator)
    """

    __tablename__ = "workflows"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), index=True)  # MCP tool name, defaulted from function_name
    function_name: Mapped[str] = mapped_column(String(255))  # Actual Python function name
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)  # UI display name; falls back to name
    description: Mapped[str | None] = mapped_column(Text, default=None)
    category: Mapped[str] = mapped_column(String(100), default="General")

    # Type discriminator: 'workflow', 'tool', or 'data_provider'
    type: Mapped[str] = mapped_column(String(20), default="workflow", index=True)

    # Organization scoping - NULL means global (available to all orgs)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
    )

    # File discovery metadata
    path: Mapped[str] = mapped_column(String(1000))  # Relative path from workspace root
    module_path: Mapped[str | None] = mapped_column(String(500), default=None)
    parameters_schema: Mapped[list] = mapped_column(JSONB, default=[])
    tags: Mapped[list] = mapped_column(JSONB, default=[])
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_orphaned: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Endpoint configuration
    endpoint_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_methods: Mapped[list] = mapped_column(JSONB, default=["POST"])
    public_endpoint: Mapped[bool] = mapped_column(Boolean, default=False)
    disable_global_key: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_mode: Mapped[str] = mapped_column(String(20), default="sync")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)  # 30 min default

    # Tool configuration (for AI agent tool calling when type='tool')
    tool_description: Mapped[str | None] = mapped_column(Text, default=None)

    # Data provider configuration (when type='data_provider')
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, default=300)  # 5 min default

    # Economics - value metrics for reporting
    time_saved: Mapped[int] = mapped_column(Integer, default=0)  # Minutes saved per execution
    value: Mapped[float] = mapped_column(Numeric(10, 2), default=0)  # Flexible value unit

    # API key (one per workflow, replaces workflow_keys table)
    api_key_hash: Mapped[str | None] = mapped_column(
        String(64), default=None
    )  # SHA-256 hash
    api_key_description: Mapped[str | None] = mapped_column(Text, default=None)
    api_key_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    api_key_created_by: Mapped[str | None] = mapped_column(String(255), default=None)
    api_key_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    api_key_last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    api_key_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Access control
    # Values: 'authenticated' (any logged-in user), 'role_based' (must have assigned role)
    access_level: Mapped[str] = mapped_column(
        String(20), default="role_based", server_default="role_based"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    organization: Mapped["Organization | None"] = relationship(
        back_populates="workflows",
        foreign_keys=[organization_id],
    )
    agents: Mapped[list["Agent"]] = relationship(
        secondary="agent_tools",
        back_populates="tools",
    )
    # Roles via junction table
    workflow_roles: Mapped[list["WorkflowRole"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
    )
    roles: Mapped[list["Role"]] = relationship(
        secondary="workflow_roles",
        viewonly=True,
    )

    __table_args__ = (
        Index(
            "ix_workflows_api_key_hash",
            "api_key_hash",
            postgresql_where=text("api_key_hash IS NOT NULL"),
        ),
        # Type index is created as a regular index via mapped_column(index=True)
        # Unique constraint on (path, function_name) for ON CONFLICT upserts
        UniqueConstraint("path", "function_name", name="workflows_path_function_key"),
    )

