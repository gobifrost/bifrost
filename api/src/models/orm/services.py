"""
Service definition and attempt ORM models.

Services are long-lived supervised executables (connections, subscriptions,
listeners) declared with @service and stored in the workflows table with
type='service'. These tables hold the service control plane:

- ServiceDefinition: durable desired state + restart policy (one per service
  workflow row). Restart/startup policy is portable content; desired state,
  enablement, and launch-suppression fields are environment/runtime state.
- ServiceAttempt: one row per supervised run. At most one live attempt per
  service, enforced by a partial unique index; all attempt updates carry the
  lease token so stale workers are fenced after reassignment.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution
    from src.models.orm.workflows import Workflow


class ServiceDefinition(Base):
    """
    Durable service definition and desired state.

    One row per @service workflow. The source identity lives on the linked
    Workflow row (path + function_name); this row owns lifecycle policy and
    desired state.
    """

    __tablename__ = "service_definitions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    solution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )

    # Operator switch. Disabled services never become eligible regardless of
    # desired_state.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Policies (portable content — round-trip in manifests).
    # startup_policy: 'automatic' | 'manual'
    # restart_policy: 'always' | 'on_failure' | 'never'
    startup_policy: Mapped[str] = mapped_column(String(20), default="automatic")
    restart_policy: Mapped[str] = mapped_column(String(20), default="always")

    # Desired state (runtime state — never serialized to portable bundles).
    # Values: 'running' | 'stopped'
    desired_state: Mapped[str] = mapped_column(String(20), default="stopped")

    # Launch suppression (runtime state). Eligibility requires
    # blocked_reason IS NULL AND restart_eligible_at <= now AND no live
    # attempt. Terminal policy outcomes (clean return under on_failure/never,
    # crash-loop entry) set blocked_reason; manual restart or source/config
    # change clears it.
    # blocked_reason: NULL | 'policy' | 'crash_loop' | 'disabled'
    blocked_reason: Mapped[str | None] = mapped_column(String(20), default=None)
    restart_eligible_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Immutable source identity pinned at claim (content hash hex). NULL until
    # the first attempt is claimed.
    current_revision: Mapped[str | None] = mapped_column(String(64), default=None)

    # Lifecycle tuning.
    graceful_shutdown_seconds: Mapped[int] = mapped_column(Integer, default=30)
    startup_grace_seconds: Mapped[int] = mapped_column(Integer, default=60)
    restart_backoff_initial_seconds: Mapped[int] = mapped_column(Integer, default=1)
    restart_backoff_max_seconds: Mapped[int] = mapped_column(Integer, default=300)
    crash_loop_max_restarts: Mapped[int] = mapped_column(Integer, default=5)
    crash_loop_window_seconds: Mapped[int] = mapped_column(Integer, default=300)

    # Audit
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
    workflow: Mapped["Workflow"] = relationship(foreign_keys=[workflow_id])
    organization: Mapped["Organization | None"] = relationship(foreign_keys=[organization_id])
    solution: Mapped["Solution | None"] = relationship(foreign_keys=[solution_id])
    attempts: Mapped[list["ServiceAttempt"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_service_definitions_organization_id", "organization_id"),
        Index("ix_service_definitions_solution_id", "solution_id"),
        Index("ix_service_definitions_enabled", "enabled"),
    )


# Attempt states that own a child process or lease. At most one row in these
# states may exist per service (partial unique index below). Terminal states
# are 'stopped' (requested or clean) and 'failed' (unexpected exit).
_SERVICE_LIVE_STATES = ("starting", "running", "stopping")


class ServiceAttempt(Base):
    """
    One supervised run of a service.

    Immutable once terminal (stopped/failed) apart from final bookkeeping.
    Every state update must present the lease token; updates from a
    superseded attempt are rejected.
    """

    __tablename__ = "service_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[str | None] = mapped_column(String(64), default=None)
    worker_id: Mapped[str | None] = mapped_column(String(255), default=None)

    # Fencing + liveness.
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Values: 'starting' | 'running' | 'stopping' | 'stopped' | 'failed'
    state: Mapped[str] = mapped_column(String(20), default="starting")

    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    stop_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    exit_code: Mapped[int | None] = mapped_column(Integer, default=None)
    exit_reason: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    restart_number: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    service: Mapped["ServiceDefinition"] = relationship(back_populates="attempts")

    __table_args__ = (
        Index("ix_service_attempts_service_id", "service_id"),
        Index("ix_service_attempts_state", "state"),
        # Singleton enforcement: at most one live attempt per service.
        Index(
            "uq_service_attempts_single_live",
            "service_id",
            unique=True,
            postgresql_where=text("state IN ('starting', 'running', 'stopping')"),
        ),
    )


class ServiceLog(Base):
    """
    One persisted service log line (trailing bounded view per service).

    Drained periodically from the attempt-scoped Redis streams
    (``bifrost:service-logs:{attempt_id}``) by the owning worker's claim
    loop — never terminal-only, since service attempts are long-lived.
    Retention is a per-service trailing cap enforced inside the flush
    (newest N rows per service survive); attempt history outlives raw logs.
    """

    __tablename__ = "service_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    level: Mapped[str] = mapped_column(String(20), default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index("ix_service_logs_service_time", "service_id", "timestamp", "id"),
        Index("ix_service_logs_attempt_id", "attempt_id"),
    )
