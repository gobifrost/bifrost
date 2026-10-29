"""Privileged principals and privileged role-assignment changes.

A principal is privileged when anything they hold, at any boundary, is in
``PRIVILEGED_PERMISSIONS`` or is the Platform Admin wildcard. Boundaries do
not narrow this: someone who can change configs in Org B is a protected
target when acted on from Org A too, because taking over their account
takes over what they hold everywhere.
"""

from __future__ import annotations

from collections.abc import Iterable

from shared.builtin_roles import WILDCARD_PERMISSION
from src.models.contracts.permissions import PRIVILEGED_PERMISSIONS


def is_privileged_principal(held_permissions: Iterable[str]) -> bool:
    """Whether ``held_permissions`` (the base role plus every additional role
    at every boundary, as ``AuthorizationContext.held_permissions`` gives it)
    make the person privileged."""
    return any(
        permission == WILDCARD_PERMISSION or permission in PRIVILEGED_PERMISSIONS
        for permission in held_permissions
    )


def role_assignment_requires_privileged_access(
    *, role_permissions: Iterable[str], target_permissions: Iterable[str]
) -> bool:
    """Whether changing this assignment needs ``privilegedaccess.readwrite``.

    ``roleassignments.readwrite`` is sufficient for ordinary roles, including
    roles that carry ordinary permissions. Assigning or removing a privileged
    role, or changing an account that already holds one, additionally needs
    privileged access at that account's organization.
    """
    return is_privileged_principal(role_permissions) or is_privileged_principal(
        target_permissions
    )
