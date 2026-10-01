"""Privileged principals and the Platform Operator's role-assignment rule.

Not enforced: nothing in request handling calls these yet. R3 uses them when
it converts the user and role-assignment routes.

A principal is privileged when anything they hold, at any boundary, is in
``PRIVILEGED_PERMISSIONS`` or is the Platform Admin wildcard. Boundaries do
not narrow this: someone who can change configs in Org B is a protected
target when acted on from Org A too, because taking over their account
takes over what they hold everywhere.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from shared.builtin_roles import WILDCARD_PERMISSION, is_builtin_role_id
from src.models.contracts.permissions import PRIVILEGED_PERMISSIONS


def is_privileged_principal(held_permissions: Iterable[str]) -> bool:
    """Whether ``held_permissions`` (the base role plus every additional role
    at every boundary, as ``AuthorizationContext.held_permissions`` gives it)
    make the person privileged."""
    return any(
        permission == WILDCARD_PERMISSION or permission in PRIVILEGED_PERMISSIONS
        for permission in held_permissions
    )


def operator_assignable_role(
    *,
    role_id: UUID,
    role_permissions: frozenset[str],
    target_permissions: Iterable[str],
) -> bool:
    """Whether a Platform Operator may assign or remove ``role_id`` on a user
    who holds ``target_permissions``.

    Operators hold ``roleassignments.readwrite`` only for sharing entities:
    the role must carry no permissions at all (a pure app/form/workflow/agent
    access role), must not be a builtin role (Platform Admin stores no
    permission rows, so the permission check alone would admit it), and the
    target must not be a privileged principal.
    """
    return (
        not is_builtin_role_id(role_id)
        and not role_permissions
        and not is_privileged_principal(target_permissions)
    )
