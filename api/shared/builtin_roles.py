"""Fixed identities and seeded permissions for the R2b base-roles model.

Four built-in roles exist, each with a fixed UUID so migrations, seed data,
and runtime code agree on identity without a name lookup:

- **Platform Admin** (additional, builtin): full access. Holds the wildcard
  permission ``"*"`` as its one ``role_permissions`` row. Held as an
  additional role at the ``platform`` boundary, never as a base role.
- **User** (base, builtin): the default base role.
  Its permission set is *derived* from the access list
  (``src.services.access_list.ACCESS_LIST``) rather than hand-maintained,
  so it can never silently drift from what an authenticated user can
  already reach today. See ``derive_user_base_permissions``.
- **Platform Operator** (builtin, not base): read visibility into managed
  organizations plus user support, role assignment and running workflows
  there (see ``PLATFORM_OPERATOR_PERMISSIONS``). Held as an additional role
  by people in the provider organization only.
- **Secrets Reader** (builtin, not base): holds only ``secrets.read``, the
  one permission the Platform Admin wildcard does not include, so decrypting
  a secret always takes an explicit assignment.

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
# The Secrets Reader role. Its identifiers say "decryption", not "secret":
# CodeQL's sensitive-data heuristic treats a value held in a secret-named
# identifier as a secret, and role ids are logged.
DECRYPTION_ROLE_ID = UUID("00000000-0000-0000-0000-000000000008")

# The wildcard permission representing Platform Admin's full access. Stored
# as Platform Admin's one ``role_permissions`` row; no other role can hold it
# (``parse_permission`` rejects it).
WILDCARD_PERMISSION = "*"

BUILTIN_ROLE_IDS = frozenset(
    {PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID, DECRYPTION_ROLE_ID}
)

# The one builtin role that is a base role. A base role is User or a custom
# role; Platform Admin is held as an additional role, never a base role.
BASE_ROLE_IDS = frozenset({USER_ROLE_ID})


def is_builtin_role_id(role_id: UUID) -> bool:
    """Whether `role_id` is one of the builtin roles.

    Resource-assignment endpoints (role-to-agent/app/workflow/user/form)
    refuse builtin role ids with 409 — there's no UI yet (R3a) for
    assigning entities to a builtin role, and their
    access is meant to come from their permission set, not per-entity
    role bindings.
    """
    return role_id in BUILTIN_ROLE_IDS


# The actions the User base role may hold: reading and launching, never
# writing.
_USER_BASE_ACTIONS = frozenset({"read", "readbasic", "execute"})


def derive_user_base_permissions(access_list: "list[AccessEntry]") -> frozenset[str]:
    """The User base role's permission set, derived from the access list.

    Exactly the permissions of every access-list entry whose deciding entry
    (an MCP tool bound to a REST route through the operation catalog is
    decided by that route's entry, not by the MCP transport floor) is:
    - ``access_class == permission``
    - ``current_gate == authenticated`` (no scope-bypass modifier layered on)
    - not narrowed by an inline check to superusers or scope-bypass callers
      (``inline_effect`` is not ``deny_unless_superuser``/``deny_unless_bypass``)
    - ``intended_change is None`` (a permanent, not a transitional, grant)
    - ``boundary == "organization"``
    - action ``read``, ``readbasic`` or ``execute`` (never a write, and
      never ``.all``: other people's private items)

    These are exactly the reads and launches any authenticated user can
    already make today with no admin/provider-org bypass involved, so
    granting them via the User base role changes nothing about who can do
    what.
    """
    from src.models.contracts.access_list import AccessClass, CurrentGate, InlineEffect
    from src.models.contracts.permissions import parse_permission
    from src.services.access_list import effective_entries

    narrowed = {InlineEffect.DENY_UNLESS_SUPERUSER, InlineEffect.DENY_UNLESS_BYPASS}
    permissions: set[str] = set()
    for entry in effective_entries(access_list):
        if entry.access_class != AccessClass.PERMISSION:
            continue
        if entry.current_gate != CurrentGate.AUTHENTICATED:
            continue
        if entry.inline_effect in narrowed:
            continue
        if entry.intended_change is not None:
            continue
        if entry.boundary != "organization":
            continue
        assert entry.permission is not None
        parsed = parse_permission(entry.permission)
        if parsed.extended or parsed.action not in _USER_BASE_ACTIONS:
            continue
        permissions.add(entry.permission)
    return frozenset(permissions)


# Frozen literal copy of what `derive_user_base_permissions(ACCESS_LIST)`
# produces as of the latest migration that changes it
# (20261009_graph_permission_names). See the module docstring for why this
# is not computed live. `tests/unit/test_builtin_roles.py` asserts the two
# stay identical.
USER_BASE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "agentruns.read",
        "agents.readbasic",
        "apps.readbasic",
        "executions.readbasic",
        "forms.readbasic",
        "knowledge.read",
        "mcp.readbasic",
        "metrics.read",
        "settings.readbasic",
    }
)

# Frozen copy of what the latest migration that changes it
# (20261009_graph_permission_names) leaves; see
# `tests/unit/test_builtin_roles.py`. `configs.read` and `integrations.read`
# are metadata only: decrypting a secret is `secrets.read`, which this role
# never holds. `roleassignments.readwrite` is limited at the cutover to roles
# that carry no permissions, on users who are not privileged
# (`src.services.authorization.privilege.operator_assignable_role`).
PLATFORM_OPERATOR_PERMISSIONS: frozenset[str] = frozenset(
    {
        "agents.readbasic",
        "forms.readbasic",
        "apps.readbasic",
        "workflows.read",
        "workflows.execute",
        "executions.readbasic",
        "agentruns.read",
        "configs.read",
        "integrations.read",
        "metrics.read",
        "organizations.read",
        "users.read",
        "users.readwrite",
        "roleassignments.read",
        "roleassignments.readwrite",
    }
)

# Frozen copy of what 20261001_r3a_operator_perms writes; see
# `tests/unit/test_builtin_roles.py`.
DECRYPTION_ROLE_PERMISSIONS: frozenset[str] = frozenset({"secrets.read"})
