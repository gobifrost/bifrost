"""Reusable execution worker incarnations (one Kubernetes Job lifetime each)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class ExecutionWorker(Base):
    """One executor incarnation.

    ``form`` is ``job`` (a controller-created Kubernetes Job and the pod it
    admitted; drains itself when idle) or ``static`` (a long-running
    Deployment or Compose container that registers itself and never idles
    out).

    States: ``reserved`` (row committed, Job not yet bound) → ``starting``
    (Job UID bound) → ``ready`` (pod admitted, claiming) → ``draining`` (no
    new claims) → ``retired``; or ``lost`` (stopped heartbeating, start
    deadline missed, or its Job vanished).
    """

    __tablename__ = "execution_workers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    form: Mapped[str] = mapped_column(String(20), nullable=False, default="job")
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved")
    resource_class: Mapped[str] = mapped_column(String(50), nullable=False, default="standard")
    slots: Mapped[int] = mapped_column(Integer, nullable=False)
    kubernetes_namespace: Mapped[str | None] = mapped_column(String(63), nullable=True)
    kubernetes_job_name: Mapped[str | None] = mapped_column(String(63), nullable=True, unique=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kubernetes_job_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kubernetes_pod_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The physical launcher and the API replica controlling it are distinct
    # identities.  A websocket is only a transport for this persisted fence.
    remote_launcher_id: Mapped[UUID | None] = mapped_column(unique=True, nullable=True)
    remote_owner_id: Mapped[UUID | None] = mapped_column(nullable=True)
    remote_session_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    remote_owner_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # SHA-256 of the authenticated one-time hello nonce. Never persist a
    # reusable proof or the platform secret itself.
    remote_auth_nonce_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Signed monotonically increasing launch-process hello sequence. This is
    # durable replay protection across API replicas, unlike a last-nonce check.
    remote_hello_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    # Existing rows retain the prior all-handler behavior during rollout.
    # New worker forms narrow the list when they are registered or reserved.
    supported_handlers: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: [
            "workflow.execute",
            "agent.run",
            "agent.summarize",
            "agent.summarize_backfill",
        ],
        server_default=text(
            "'[\"workflow.execute\", \"agent.run\", \"agent.summarize\", "
            "\"agent.summarize_backfill\"]'::jsonb"
        ),
    )
    active_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Over its memory threshold: claims nothing, so its free slots are not
    # capacity the controller can count on.
    memory_pressured: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Memory this executor may hand to work, and how much its held work
    # requested (src.services.memory_requests). None until it reports.
    memory_budget_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    memory_reserved_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    # A Job worker's container memory limit; None for the standard size.
    memory_limit_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # A worker launched for one large dispatch claims it before anything else.
    reserved_for_dispatch_id: Mapped[UUID | None] = mapped_column(nullable=True)
    runs_handled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The build this executor runs (BIFROST_VERSION), reported when it starts.
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The worker controller instance that created this Job; with ``version``
    # it decides when an upgrade replaces the Job (worker_pool.controller).
    launched_by: Mapped[UUID | None] = mapped_column(nullable=True)
    # The package generation this executor has applied (see PackageChange).
    package_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_claim_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_execution_workers_state", "state"),
        Index("ix_execution_workers_remote_owner_lease", "remote_owner_lease_expires_at"),
    )


class ExecutionWorkerClaim(Base):
    """Which executor took which run, so a lost executor's runs can be ended.

    Written in the pool claim's transaction; removed with its executor row.
    """

    __tablename__ = "execution_worker_claims"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    worker_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_workers.id", ondelete="CASCADE"), nullable=False
    )
    family: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )

    __table_args__ = (
        Index("ix_execution_worker_claims_worker_subject", "worker_id", "subject_id"),
    )
