"""
Agent, AgentTool, AgentDelegation, AgentRole, Conversation, and Message ORM models.

Represents AI agents, their tool/delegation relationships, and chat conversations.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SQLAlchemyEnum, ForeignKey, Index, Integer, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.enums import AgentAccessLevel, MessageRole
from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.ai_usage import AIUsage
    from src.models.orm.organizations import Organization
    from src.models.orm.users import Role, User
    from src.models.orm.workflows import Workflow
    from src.models.orm.workspaces import Workspace


# Execution-resolution entity — access via AgentRepository (OrgScopedRepository).
# See api/src/repositories/README.md.
class Agent(Base):
    """Agent database table."""

    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    logo_content_type: Mapped[str | None] = mapped_column(String(50), default=None)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    channels: Mapped[list] = mapped_column(JSONB, default=["chat"])
    access_level: Mapped[AgentAccessLevel] = mapped_column(
        SQLAlchemyEnum(
            AgentAccessLevel,
            name="agent_access_level",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=AgentAccessLevel.ROLE_BASED,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None
    )
    # Solution scoping - NULL means ad-hoc _repo/ entity. NOT NULL = solution-
    # managed (read-only on platform). See solutions.py / success-criteria §3.2.
    solution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
        index=True,
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Knowledge namespaces this agent can search (RAG)
    knowledge_sources: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default='{}'
    )
    # System tools enabled for this agent (e.g., ["execute_workflow", "search_knowledge"])
    system_tools: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default='{}'
    )
    # LLM configuration overrides (null = use global config)
    llm_model: Mapped[str | None] = mapped_column(String(100), default=None)
    llm_max_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    max_iterations: Mapped[int | None] = mapped_column(Integer, default=50)
    max_token_budget: Mapped[int | None] = mapped_column(Integer, default=100000)
    max_run_timeout: Mapped[int | None] = mapped_column(Integer, default=None)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
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
    organization: Mapped["Organization | None"] = relationship(back_populates="agents")
    owner: Mapped["User | None"] = relationship(foreign_keys=[owner_user_id])
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    # Tools via junction table
    tools: Mapped[list["Workflow"]] = relationship(
        secondary="agent_tools",
        back_populates="agents",
    )
    # Delegations (agents this agent can delegate to)
    delegated_agents: Mapped[list["Agent"]] = relationship(
        secondary="agent_delegations",
        primaryjoin="Agent.id == agent_delegations.c.parent_agent_id",
        secondaryjoin="Agent.id == agent_delegations.c.child_agent_id",
        backref="parent_agents",
    )
    # Roles via junction table
    roles: Mapped[list["Role"]] = relationship(
        secondary="agent_roles",
        back_populates="agents",
    )
    # External MCP connections this agent has been explicitly granted access
    # to. Default for new agents is empty (deny-by-default); the migration
    # that introduces ``agent_mcp_connections`` backfills grants for existing
    # rows so the rollout preserves current behavior.
    #
    # Untyped on purpose: importing MCPConnection (even under TYPE_CHECKING)
    # closes a CodeQL-flagged cycle through external_mcp → organizations →
    # agents. SQLAlchemy resolves the string class name at mapper config
    # time, so the runtime is unaffected.
    mcp_connections = relationship(
        "MCPConnection",
        secondary="agent_mcp_connections",
    )

    __table_args__ = (
        Index("ix_agents_organization_id", "organization_id"),
        Index("ix_agents_is_active", "is_active"),
        Index("ix_agents_owner_user_id", "owner_user_id"),
    )

    # Computed properties for Pydantic from_attributes compatibility
    @property
    def tool_ids(self) -> list[str]:
        """List of workflow IDs used as tools."""
        return [str(t.id) for t in self.tools]

    @property
    def delegated_agent_ids(self) -> list[str]:
        """List of agent IDs this agent can delegate to."""
        return [str(a.id) for a in self.delegated_agents]

    @property
    def role_ids(self) -> list[str]:
        """List of role IDs that can access this agent."""
        return [str(r.id) for r in self.roles]

    @property
    def mcp_connection_ids(self) -> list[str]:
        """List of MCP connection UUIDs granted to this agent."""
        return [str(c.id) for c in self.mcp_connections]


class AgentTool(Base):
    """Agent-Tool (Workflow) association table."""

    __tablename__ = "agent_tools"

    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )


class AgentDelegation(Base):
    """Agent-to-Agent delegation association table."""

    __tablename__ = "agent_delegations"

    parent_agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    child_agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )


class AgentRole(Base):
    """Agent-Role association table."""

    __tablename__ = "agent_roles"

    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True
    )
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[str] = mapped_column(String(255))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )


class Conversation(Base):
    """Conversation database table."""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_id: Mapped[UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    channel: Mapped[str] = mapped_column(String(50), default="chat")
    title: Mapped[str | None] = mapped_column(String(500), default=None)
    current_model: Mapped[str | None] = mapped_column(String(255), default=None)
    # Active branch tip — drives history loading. NULL on empty conversation.
    active_leaf_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    instructions: Mapped[str | None] = mapped_column(Text, default=None)
    # Lossless compaction checkpoint (Chat V2 M5, §4.1). The message rows are
    # never modified; these fields only describe how the working context is
    # folded. ``compaction_through_sequence`` is the highest message sequence
    # folded into ``compaction_summary``; later messages stay verbatim.
    compaction_summary: Mapped[str | None] = mapped_column(Text, default=None)
    compaction_through_sequence: Mapped[int | None] = mapped_column(
        Integer, default=None
    )
    compaction_original_tokens: Mapped[int | None] = mapped_column(
        Integer, default=None
    )
    extra_data: Mapped[dict] = mapped_column(JSONB, default={})  # Channel-specific metadata
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
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
    agent: Mapped["Agent | None"] = relationship(back_populates="conversations")
    user: Mapped["User"] = relationship()
    workspace: Mapped["Workspace | None"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence",
        foreign_keys="Message.conversation_id",
    )
    ai_usages: Mapped[list["AIUsage"]] = relationship(back_populates="conversation")

    __table_args__ = (
        Index("ix_conversations_user_id", "user_id"),
        Index("ix_conversations_agent_id", "agent_id"),
        Index("ix_conversations_workspace_id", "workspace_id"),
        Index("ix_conversations_created_at", "created_at"),
    )


class Message(Base):
    """Message database table."""

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        SQLAlchemyEnum(
            MessageRole,
            name="message_role",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    content: Mapped[str | None] = mapped_column(Text, default=None)
    # For assistant messages requesting tool calls: [{id, name, arguments}]
    tool_calls: Mapped[list | None] = mapped_column(JSONB, default=None)
    # For tool result messages - which tool call this responds to
    tool_call_id: Mapped[str | None] = mapped_column(String(255), default=None)
    tool_name: Mapped[str | None] = mapped_column(String(255), default=None)
    # Execution ID for fetching logs from tool executions
    execution_id: Mapped[str | None] = mapped_column(String(36), default=None)
    # Client-generated ID for optimistic update reconciliation
    local_id: Mapped[str | None] = mapped_column(String(36), default=None)
    # Tool call state tracking (for TOOL_CALL role)
    tool_state: Mapped[str | None] = mapped_column(String(20), default=None)  # running, completed, error
    tool_result: Mapped[dict | None] = mapped_column(JSONB, default=None)  # Result data from tool execution
    tool_input: Mapped[dict | None] = mapped_column(JSONB, default=None)  # Input arguments for tool call
    # Token usage metrics
    token_count_input: Mapped[int | None] = mapped_column(Integer, default=None)
    token_count_output: Mapped[int | None] = mapped_column(Integer, default=None)
    model: Mapped[str | None] = mapped_column(String(100), default=None)
    cost_tier: Mapped[str | None] = mapped_column(String(20), default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    # Order within conversation
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # Parent in the message tree. NULL only for the root message.
    parent_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages",
        foreign_keys="Message.conversation_id",
    )
    attachments: Mapped[list["MessageAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageAttachment.created_at",
    )

    __table_args__ = (
        Index("ix_messages_conversation_sequence", "conversation_id", "sequence"),
        Index("ix_messages_parent_message_id", "parent_message_id"),
    )


class MessageAttachment(Base):
    """User-uploaded file attached to a chat message.

    Content lives in S3 under ``_attachments/{conversation_id}/{uuid}_{filename}``
    (see AttachmentService); only metadata + extracted text live in the DB.
    Bound to a message when the message is sent (see §3.3 of the chat UX spec).
    """

    __tablename__ = "message_attachments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # Nullable until bound: an attachment is uploaded to a conversation first,
    # then bound to the user message at send time.
    message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Text extracted server-side (PDFs, CSVs, text files). NULL for images.
    extracted_text: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    # Relationships
    message: Mapped["Message | None"] = relationship(back_populates="attachments")

    __table_args__ = (
        Index("ix_message_attachments_message_id", "message_id"),
        Index("ix_message_attachments_conversation_id", "conversation_id"),
    )
