"""Resolve SDK scopes from the authenticated caller's organization reach."""

from __future__ import annotations

from typing import Final
from uuid import UUID

from shared.access_checks import note_decision
from src.services.authorization.enforce import Caller, user_reach


class ScopeNotAllowed(Exception):
    """Raised when a requested scope falls outside the original user's reach."""


class _Unset:
    """Sentinel for an omitted scope, distinct from explicit Global."""

    _instance: "_Unset | None" = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET: Final[_Unset] = _Unset()
RequestedScope = _Unset | None | UUID


def _default_scope(caller: Caller) -> UUID | None:
    runtime = caller.principal.runtime_authority
    if runtime is not None:
        return runtime.organization_id
    if caller.ctx is not None:
        return caller.ctx.home_organization_id
    return caller.principal.organization_id


def shared_read_organization(
    caller: Caller,
    resource_organization_id: UUID | None,
    *,
    selected_organization_id: RequestedScope = UNSET,
) -> UUID | None:
    """The permission target for a resource read with shared Global defaults.

    An organization row uses its own target. A Global row reached as the
    selected organization's shared fallback is read at that organization.
    Explicit Global remains Global. Mutations always use the persisted row's
    organization and must not use this helper.
    """
    if resource_organization_id is not None:
        return resource_organization_id
    return (
        _default_scope(caller)
        if isinstance(selected_organization_id, _Unset)
        else selected_organization_id
    )


def resolve_effective_scope(
    *, caller: Caller, requested_scope: RequestedScope = UNSET
) -> UUID | None:
    """Resolve an SDK target using reach only; operation permission is separate."""
    resolved = (
        _default_scope(caller)
        if isinstance(requested_scope, _Unset)
        else requested_scope
    )
    if caller.ctx is not None:
        from src.services.authorization.explain import RunUser, check_target

        runtime = caller.principal.runtime_authority
        run_user = (
            RunUser(caller.ctx.user_id, caller.ctx, None, is_active=caller.principal.is_active)
            if runtime is None else runtime.run_user
        )
        note_decision(
            "scope_switch", resolved, run_user=run_user,
            trace=check_target(run_user, None if runtime is None else runtime.powers, resolved, None),
        )
    if not user_reach(caller).covers(resolved):
        raise ScopeNotAllowed("Requested scope is outside the caller's organization reach")
    return resolved
