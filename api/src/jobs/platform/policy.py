"""Pure-data policy contracts for scheduler-owned platform jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PlatformJobPolicy:
    timeout_seconds: int
    max_attempts: int = 2
    max_concurrency: int | None = None
    retry_on_runner_loss: bool = True
    retry_on_failure: bool = False
    min_memory_headroom_mb: int = 256
    hard_memory_ratio: float = 0.95
    allow_running_cancellation: bool = False
    execution_class: Literal["default", "build"] = "default"
