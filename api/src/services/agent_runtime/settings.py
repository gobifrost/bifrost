"""Central tunables for the durable agent runtime.

Lease durations stay with their owners (consumer heartbeat, scheduler), but
every bound on delegation shape lives here so fan-out authorization,
nesting, and concurrency fail closed on one shared contract. Resolved values
are journaled with each fan-out intent, freezing them for that join.
"""

from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MAX_FANOUT_CHILDREN = _int_env("BIFROST_MAX_FANOUT_CHILDREN", 10)
"""Maximum child tasks in one ``delegate_agents`` call."""

MAX_FANOUT_ACTIVE_CHILDREN = _int_env("BIFROST_MAX_FANOUT_ACTIVE_CHILDREN", 20)
"""Maximum simultaneously unfinished children per parent run."""

MAX_DELEGATION_DEPTH = _int_env("BIFROST_MAX_DELEGATION_DEPTH", 5)
"""Maximum nesting depth for durable delegation trees."""


def fanout_policy_snapshot() -> dict[str, int]:
    """Resolved fan-out bounds, journaled immutably with each join."""
    return {
        "max_children": MAX_FANOUT_CHILDREN,
        "max_active_children": MAX_FANOUT_ACTIVE_CHILDREN,
        "max_depth": MAX_DELEGATION_DEPTH,
    }


DEFAULT_RUN_TIMEOUT_SECONDS = 1800
"""Per-attempt safety net when neither snapshot nor Agent sets a timeout."""


def effective_run_timeout_seconds(
    snapshot_timeout: int | float | None,
    agent_timeout: int | float | None,
    *,
    default: float = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> float | None:
    """Resolve the active-run safety limit for one attempt.

    Snapshot wins over live Agent configuration. ``0`` disables the timeout
    (consistent with workflow timeouts); unset falls back to ``default``.
    ``None`` means disabled — the caller waits on cancellation only.
    """
    configured = snapshot_timeout if snapshot_timeout is not None else agent_timeout
    if configured is None:
        return float(default)
    if configured <= 0:
        return None
    return float(configured)
