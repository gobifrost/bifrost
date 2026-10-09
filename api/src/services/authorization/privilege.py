"""Privileged principals and the role-assignment grant ceiling.

The identity routes (users, role assignments) use these through
``src.services.authorization.enforce`` and
``src.services.user_role_assignments``.

A principal is privileged when anything they hold, at any boundary, is in
``PRIVILEGED_PERMISSIONS`` or is the Platform Admin wildcard. Boundaries do
not narrow this: someone who can change configs in Org B is a protected
target when acted on from Org A too, because taking over their account
takes over what they hold everywhere.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from shared import access_checks
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


def may_change_role_assignment(
    *,
    actor_is_platform_admin: bool,
    role_id: UUID,
    role_permissions: frozenset[str],
    target_permissions: Iterable[str],
) -> bool:
    """The grant ceiling: whether the actor may grant or remove ``role_id``
    (as a base role or an additional role) on a user who holds
    ``target_permissions``.

    A Platform Admin may change any assignment (the callers still apply
    their own rules about which builtin roles can be granted where). Every
    other actor, whatever identity permissions their roles give them, is
    held to the Platform Operator rule (``operator_assignable_role``): only
    roles that carry no permissions and are not builtin, and never on a
    privileged user. Until a reviewed design for delegating "manage
    unprivileged roles" exists, no delegate can hand out a permission.
    """
    assignable = operator_assignable_role(
        role_id=role_id,
        role_permissions=role_permissions,
        target_permissions=target_permissions,
    )
    if actor_is_platform_admin and not assignable:
        # No organization is known here: the assignment is noted at Global.
        access_checks.note_power("privilegedaccess.readwrite", None, subject=f"role:{role_id}")
        return True
    return actor_is_platform_admin or assignable
