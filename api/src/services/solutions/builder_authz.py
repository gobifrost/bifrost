"""Builder capability authorization (``solutions.build``).

The action grant comes from the principal's role-derived authorization scopes.
Organization reach and per-Solution access remain separate gates.
"""

from __future__ import annotations

from shared.authorization_scopes import SOLUTIONS_BUILD_SCOPE
from src.core.principal import UserPrincipal


def can_build(principal: UserPrincipal) -> bool:
    """Whether a human principal may enter the Builder capability boundary."""

    return not principal.is_external and principal.has_scope(SOLUTIONS_BUILD_SCOPE)
