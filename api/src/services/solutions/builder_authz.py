"""Builder capability authorization (``solutions.build``).

Single decision point for "may this user use the private-Solution builder"
(2026-07-25 private-solution-builder spec, "Builder permission"). Routers must
call this service rather than scattering ``role.permissions.get(...)`` checks;
when the #473 scope catalog lands, only this module changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

SOLUTIONS_BUILD_PERMISSION = "solutions.build"


def can_build(
    *,
    is_platform_admin: bool,
    is_external: bool,
    role_permissions: Iterable[Mapping[str, object]],
) -> bool:
    """Decide builder access from JWT-derived flags and the user's role rows.

    External users are denied even if accidentally assigned a granting role;
    platform admins bypass the role check. ``role_permissions`` is the
    ``Role.permissions`` JSON of each role assigned to the user.
    """
    if is_external:
        return False
    if is_platform_admin:
        return True
    return any(bool(perms.get(SOLUTIONS_BUILD_PERMISSION)) for perms in role_permissions)
