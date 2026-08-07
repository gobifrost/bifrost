"""Platform-admin contracts for scheduler diagnostics."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SchedulerLeaderStatus(BaseModel):
    owner_id: str | None = None
    lease_expires_at: datetime | None = None
    healthy: bool = False


class SchedulerReplicaStatus(BaseModel):
    id: str
    hostname: str
    pid: int
    job_slots: int
    is_leader: bool
    online: bool
    started_at: datetime
    last_heartbeat_at: datetime
    memory_current_bytes: int | None = None
    memory_limit_bytes: int | None = None
    active_platform_job_ids: list[UUID]
    active_platform_jobs: int = 0


class SchedulerTaskRunStatus(BaseModel):
    id: UUID
    status: str
    leader_owner_id: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    summary: str | None = None
    error_message: str | None = None
    platform_job_id: UUID | None = None
    platform_job_status: str | None = None
    platform_job_memory_start_bytes: int | None = None
    platform_job_memory_peak_bytes: int | None = None
    platform_job_memory_limit_bytes: int | None = None


class SchedulerTaskStatus(BaseModel):
    task_id: str
    name: str
    schedule: str
    execution_mode: str
    enabled: bool
    next_run_at: datetime | None = None
    last_run: SchedulerTaskRunStatus | None = None


class SchedulerCapacityStatus(BaseModel):
    replicas_online: int
    slots_total: int
    slots_running: int
    jobs_queued: int
    jobs_waiting_for_memory: int
    oldest_queued_seconds: float | None = None
    max_memory_utilization_percent: float | None = Field(default=None, ge=0)


class SystemDiagnosticLogPublic(BaseModel):
    id: int
    source: str
    level: str
    code: str
    message: str
    scheduler_run_id: UUID | None = None
    platform_job_id: UUID | None = None
    created_at: datetime


class SchedulerTaskRunDetail(SchedulerTaskRunStatus):
    logs: list[SystemDiagnosticLogPublic]


class SchedulerTaskHistoryResponse(BaseModel):
    task_id: str
    name: str
    runs: list[SchedulerTaskRunDetail]


class SchedulerDiagnosticsResponse(BaseModel):
    generated_at: datetime
    leader: SchedulerLeaderStatus
    capacity: SchedulerCapacityStatus
    replicas: list[SchedulerReplicaStatus]
    tasks: list[SchedulerTaskStatus]
