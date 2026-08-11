"""Builder capability authorization (``solutions.build``).

The action grant comes from the principal's role-derived authorization scopes.
Organization reach and per-Solution access remain separate gates.
"""

from __future__ import annotations

from shared.authorization_scopes import (
    ORGANIZATION_IMPERSONATION_SCOPE,
    SOLUTIONS_BUILD_SCOPE,
)
from src.core.principal import UserPrincipal


def can_build(principal: UserPrincipal) -> bool:
    """Whether a human principal may enter the Builder capability boundary."""

    return not principal.is_external and principal.has_scope(SOLUTIONS_BUILD_SCOPE)


def can_support_builds(principal: UserPrincipal) -> bool:
    """Whether the caller may deliberately enter the provider-wide support view."""

    return not principal.is_external and (
        principal.is_platform_admin
        or (
            principal.is_provider_org
            and principal.has_scope(ORGANIZATION_IMPERSONATION_SCOPE)
        )
    )
