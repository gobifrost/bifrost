"""Fixed identities and seeded permissions for the R2b base-roles model.

Three built-in roles exist, each with a fixed UUID so migrations, seed data,
and runtime code agree on identity without a name lookup:

- **Platform Admin** (base, builtin): full access. Represented in code as
  the wildcard permission ``"*"`` — no ``role_permissions`` rows are stored
  for it.
- **User** (base, builtin): the default role every non-admin user holds.
  Its permission set is *derived* from the access list
  (``src.services.access_list.ACCESS_LIST``) rather than hand-maintained,
  so it can never silently drift from what an authenticated user can
  already reach today. See ``derive_user_base_permissions``.
- **Platform Operator** (builtin, not base): seeded but assigned to
  nobody in this PR. Read-only visibility into managed organizations.

Migrations must not import live application code (a historical migration
has to keep producing the same rows regardless of later refactors), so the
migration that seeds these roles carries a frozen literal copy of
``USER_BASE_PERMISSIONS`` rather than calling ``derive_user_base_permissions``
at upgrade time. A unit test asserts the two stay identical; if they ever
diverge, the fix is either to correct the frozen copy (in a new migration)
or to reclassify the access-list entry that moved — never to silently
change history's meaning by pointing the migration at live code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from src.models.contracts.access_list import AccessEntry

# Fixed identities, following the platform's existing well-known-UUID
# convention (see api/src/core/constants.py: system user = ...0001,
# provider org = ...0002). NOT ...0003/...0004: those ids were used (and
# are now permanently forbidden — see
# tests/e2e/platform/test_withdrawn_builder_migrations.py) by the
# withdrawn, unfinished Builder feature's Platform Admin/Platform Operator
# roles (alembic/versions/20260807_withdraw_unfinished_builder.py deletes
# any row at those ids on upgrade).
PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")

# The wildcard permission representing Platform Admin's full access. Never
# stored as a ``role_permissions`` row — Platform Admin has none.
WILDCARD_PERMISSION = "*"

BUILTIN_ROLE_IDS = frozenset(
    {PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID}
)

BASE_ROLE_IDS = frozenset({PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID})


def is_builtin_role_id(role_id: UUID) -> bool:
    """Whether `role_id` is one of the three builtin roles.

    Resource-assignment endpoints (role-to-agent/app/workflow/user/form)
    refuse builtin role ids with 409 — there's no UI yet (R3a) for
    assigning entities to Platform Admin/User/Platform Operator, and their
    access is meant to come from their permission set, not per-entity
    role bindings.
    """
    return role_id in BUILTIN_ROLE_IDS


def derive_user_base_permissions(access_list: "list[AccessEntry]") -> frozenset[str]:
    """The User base role's permission set, derived from the access list.

    Exactly the permissions of every access-list entry that is:
    - ``access_class == permission``
    - ``current_gate == authenticated`` (no scope-bypass modifier layered on)
    - ``intended_change is None`` (a permanent, not a transitional, grant)
    - ``boundary == "organization"``
    - action ``read`` (permission string ends in ``.read``)

    These are exactly the reads any authenticated user can already make
    today with no admin/provider-org bypass involved, so granting them via
    the User base role changes nothing about who can do what.
    """
    from src.models.contracts.access_list import AccessClass, CurrentGate

    permissions: set[str] = set()
    for entry in access_list:
        if entry.access_class != AccessClass.PERMISSION:
            continue
        if entry.current_gate != CurrentGate.AUTHENTICATED:
            continue
        if entry.intended_change is not None:
            continue
        if entry.boundary != "organization":
            continue
        assert entry.permission is not None
        if not entry.permission.endswith(".read"):
            continue
        permissions.add(entry.permission)
    return frozenset(permissions)


# Frozen literal copy of what `derive_user_base_permissions(ACCESS_LIST)`
# produces as of this migration. See the module docstring for why this is
# not computed live. `tests/unit/test_builtin_roles.py` asserts the two
# stay identical.
USER_BASE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "agentruns.read",
        "agents.read",
        "apps.read",
        "configs.read",
        "events.read",
        "executions.read",
        "forms.read",
        "integrations.read",
        "knowledge.read",
        "mcp.read",
        "metrics.read",
        "policyrules.read",
        "roles.read",
        "settings.read",
        "tables.read",
        "workflows.read",
    }
)

# Seeded but assigned to nobody in this PR (R2b). Its user-support actions
# (reset password/MFA, resend invite, deactivate) get a permission when R3a
# converts those routes to the permission model — not invented here.
PLATFORM_OPERATOR_PERMISSIONS: frozenset[str] = frozenset(
    {
        "agents.read",
        "forms.read",
        "apps.read",
        "workflows.read",
        "executions.read",
        "agentruns.read",
        "configs.read",
        "integrations.read",
        "metrics.read",
        "organizations.read",
    }
)
