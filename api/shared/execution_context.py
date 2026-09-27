"""Fail-closed identity validation for parent-owned execution context.

The process pool forks one child per workflow execution or supervised
service and hands it the parent-owned context. Before spending any durable
side effect (Redis write, fork) the pool validates the identity-bearing
fields the child will later rely on: a malformed identity must fail the
dispatch loudly rather than let a child run under a silently downgraded
(global org, forged service, silent-``None`` Solution) scope.

This is the identity-validation half of the removed pipe-era parent
principal derivation. The pipe/stream/local-dispatch transports are gone,
and engine children now reach the parent over the worker-local engine
socket, so only the fail-closed check is still needed here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID


class ExecutionContextError(ValueError):
    """Parent execution context carries an unusable identity.

    Raised before any dispatch side effect so the pool fails loudly instead
    of running a child under a downgraded (e.g. global) scope.
    """


def _validate_org_id(raw_org_id: Any) -> None:
    """Validate the organization id, failing closed on a malformed value.

    Missing or empty means a genuinely global execution; a non-empty value
    must be a valid UUID (or ``UUID``) — silently treating junk as global
    would widen scope.
    """
    if raw_org_id is None or raw_org_id == "":
        return
    if isinstance(raw_org_id, UUID):
        return
    if isinstance(raw_org_id, str):
        try:
            UUID(raw_org_id)
        except ValueError:
            raise ExecutionContextError(
                f"execution context: organization id {raw_org_id!r} is not a "
                "valid UUID; refusing to dispatch (no silent global downgrade)"
            ) from None
        return
    raise ExecutionContextError(
        f"execution context: organization id {raw_org_id!r} is not a "
        "string; refusing to dispatch (no silent global downgrade)"
    )


def _validate_solution_id(raw: Any) -> None:
    """Validate the Solution install id, failing closed on a malformed value.

    Missing or empty means a plain (non-solution) execution. A malformed
    non-empty value fails closed — declared-connection behavior must not
    silently downgrade to the loose silent-``None`` path.
    """
    if raw is None or raw == "":
        return
    if isinstance(raw, UUID):
        return
    if isinstance(raw, str):
        try:
            UUID(raw)
        except ValueError:
            raise ExecutionContextError(
                f"execution context: solution id {raw!r} is not a valid "
                "UUID; refusing to dispatch (no silent declared-connection "
                "downgrade)"
            ) from None
        return
    raise ExecutionContextError(
        f"execution context: solution id {raw!r} is not a string; refusing "
        "to dispatch (no silent declared-connection downgrade)"
    )


def _validate_global_repo_access(raw: Any) -> None:
    """Validate the global-repo-access flag, failing closed on a non-bool.

    Missing defaults to False (the sealed-Solution posture). A
    present-but-malformed value fails closed: coercing truthy junk would
    widen a sealed install's import surface.
    """
    if isinstance(raw, bool):
        return
    raise ExecutionContextError(
        f"execution context: solution_global_repo_access {raw!r} is not a "
        "bool; refusing to dispatch (no silent import-scope downgrade)"
    )


def _validate_service(service_raw: Any) -> None:
    """Validate the parent-owned service identity block.

    A non-mapping block fails closed, and ``service_id`` must satisfy
    ``service_sdk_actor_email`` (a valid UUID) so a service write is never
    attributed to a forged or blank value.
    """
    from src.core.security import service_sdk_actor_email

    if not isinstance(service_raw, Mapping):
        raise ExecutionContextError(
            "execution context: service identity is not a mapping; refusing "
            "to dispatch"
        )
    try:
        service_sdk_actor_email(service_raw.get("service_id"))
    except ValueError as e:
        raise ExecutionContextError(
            f"execution context: malformed service identity ({e}); refusing "
            "to dispatch"
        ) from None


def validate_execution_context(context_data: Mapping[str, Any]) -> None:
    """Fail closed on an unusable parent-owned execution identity.

    Validates only the identity-bearing fields derived from parent-owned
    context (never child frames): the organization id, the optional service
    block, the Solution install id, and the global-repo-access flag. Raises
    :class:`ExecutionContextError` on a malformed value; returns ``None``
    when the context is dispatchable.
    """
    org = context_data.get("organization")
    if org is not None:
        if not isinstance(org, Mapping):
            raise ExecutionContextError(
                f"execution context: organization {org!r} is not a mapping; "
                "refusing to dispatch"
            )
        _validate_org_id(org.get("id"))

    service_raw = context_data.get("service")
    if service_raw is not None:
        _validate_service(service_raw)

    _validate_solution_id(context_data.get("solution_id"))
    _validate_global_repo_access(
        context_data.get("solution_global_repo_access", False)
    )
