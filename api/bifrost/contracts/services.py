"""Minimal CLI-side mirror of service lifecycle DTOs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

StartupPolicy = Literal["automatic", "manual"]
RestartPolicy = Literal["always", "on_failure", "never"]


class ServicePolicyUpdate(BaseModel):
    """Patch service lifecycle policy (CLI mirror)."""

    startup_policy: StartupPolicy | None = Field(default=None)
    restart_policy: RestartPolicy | None = Field(default=None)
    graceful_shutdown_seconds: int | None = Field(default=None)
    startup_grace_seconds: int | None = Field(default=None)
    restart_backoff_initial_seconds: int | None = Field(default=None)
    restart_backoff_max_seconds: int | None = Field(default=None)
    crash_loop_max_restarts: int | None = Field(default=None)
    crash_loop_window_seconds: int | None = Field(default=None)
