"""Durable agent runtime lifecycle constants and error types.

PostgreSQL is authoritative for every admitted AgentRun. These constants
define the only legal status transitions; ``run_store`` enforces them
inside fenced transactions.
"""

from __future__ import annotations

QUEUED = "queued"
RUNNING = "running"
WAITING_CHILD = "waiting_child"
WAITING_CHILDREN = "waiting_children"
SLEEPING = "sleeping"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TIMEOUT = "timeout"
BUDGET_EXCEEDED = "budget_exceeded"
CONTRACT_FAILED = "contract_failed"
RECOVERY_REQUIRED = "recovery_required"

TERMINAL_STATUSES = frozenset(
    {
        COMPLETED,
        FAILED,
        CANCELLED,
        TIMEOUT,
        BUDGET_EXCEEDED,
        CONTRACT_FAILED,
    }
)
"""Terminal states. Terminal rows never transition again."""

INACTIVE_WAIT_STATUSES = frozenset({WAITING_CHILD, WAITING_CHILDREN, SLEEPING})
"""Durable inactive states that consume no worker."""

RESUMABLE_STATUSES = frozenset({RUNNING, *INACTIVE_WAIT_STATUSES})

TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({RUNNING, CANCELLED, FAILED}),
    RUNNING: frozenset(
        {
            WAITING_CHILD,
            WAITING_CHILDREN,
            SLEEPING,
            COMPLETED,
            FAILED,
            CANCELLED,
            TIMEOUT,
            BUDGET_EXCEEDED,
            CONTRACT_FAILED,
            RECOVERY_REQUIRED,
        }
    ),
    # Waiting states wake only through their child/timer transaction.
    WAITING_CHILD: frozenset({RUNNING, CANCELLED, FAILED}),
    WAITING_CHILDREN: frozenset({RUNNING, CANCELLED, FAILED}),
    SLEEPING: frozenset({RUNNING, CANCELLED, FAILED, TIMEOUT}),
    RECOVERY_REQUIRED: frozenset({RUNNING, CANCELLED, FAILED}),
}
"""Explicit transition map. Anything absent here is rejected."""

DEFAULT_LEASE_TTL_SECONDS = 120
"""Crash-detection lease duration. Operational setting, not a user limit."""

# Stable journal event kinds. The journal powers restart recovery and the
# debugger; kinds are part of the durable contract.
JOURNAL_MODEL_REQUEST = "model_request"
JOURNAL_MODEL_RESPONSE = "model_response"
JOURNAL_MODEL_ERROR = "model_error"
JOURNAL_TOOL_CALL = "tool_call"
JOURNAL_TOOL_RESULT = "tool_result"
JOURNAL_TOOL_ERROR = "tool_error"
JOURNAL_DELEGATION = "delegation"
JOURNAL_WAIT = "wait"
JOURNAL_RESUME = "resume"
JOURNAL_LEASE_RECOVERY = "lease_recovery"
JOURNAL_VALIDATION = "validation"
JOURNAL_COMPLETION = "completion"
JOURNAL_CHECKPOINT = "checkpoint"
JOURNAL_CANCELLATION = "cancellation"
JOURNAL_TIMER = "timer"

JOURNAL_EVENT_KINDS = frozenset(
    {
        JOURNAL_MODEL_REQUEST,
        JOURNAL_MODEL_RESPONSE,
        JOURNAL_MODEL_ERROR,
        JOURNAL_TOOL_CALL,
        JOURNAL_TOOL_RESULT,
        JOURNAL_TOOL_ERROR,
        JOURNAL_DELEGATION,
        JOURNAL_WAIT,
        JOURNAL_RESUME,
        JOURNAL_LEASE_RECOVERY,
        JOURNAL_VALIDATION,
        JOURNAL_COMPLETION,
        JOURNAL_CHECKPOINT,
        JOURNAL_CANCELLATION,
        JOURNAL_TIMER,
    }
)


class RunStoreError(Exception):
    """Base error for fenced run-store transactions."""


class RunNotFoundError(RunStoreError):
    """AgentRun row does not exist."""


class RunNotClaimableError(RunStoreError):
    """Run cannot be claimed (owned lease, inactive terminal state, ...)."""


class LeaseMismatchError(RunStoreError):
    """Writer does not hold the current lease token; it is stale."""


class InvalidTransitionError(RunStoreError):
    """Requested status transition is not in the explicit transition map."""
