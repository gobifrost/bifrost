"""Rename permissions to the Graph grammar and split the everyday views

Revision ID: 20261009_graph_permission_names
Revises: 20261009_merge_integ_identity
Create Date: 2026-10-09

A permission is now ``<resource>.<action>[.all]``, and the resource never
contains a dot. Every role's rows change as follows:

- The dotted sub-resources go. Publishing an app and deploying or building
  a Solution become verbs on their resource, and user lifecycle becomes a
  resource of its own.
- On agents, apps, executions, forms, mcp and settings, ``read`` becomes
  ``readbasic``, the everyday view of items shared with the holder. The
  plain ``read`` now names the full view that only admins reach today.
- ``.all`` now means other people's private items. The old ``.all`` reads
  meant extended management detail, so each moves to the permission that
  now names that detail: the full view (``x.read``), or ``roles.read`` for
  which roles share an entity, plus ``settings.read`` for workflow keys.

Each role keeps exactly the access it had. The User and Platform Operator
roles hold only plain reads of the basic resources among these, so for them
the change is the readbasic rename.

Launching is now a permission too, so the built-in roles gain the launch
permissions for what they can already start: the User role gains Run
Workflows, Run Agents, Use AI and reading AI model information, and the
Platform Operator role gains Run Agents next to the Run Workflows it holds.

Workflow permission grants hold permission strings as well; the four
dotted sub-resource names there get the same rename. (A Solution workflow
permission request keeps its strings: an approval is bound to a digest of
them.)

The downgrade removes the launch permissions and reverses every rename. It
cannot split a merged row back:
``reports.read.all``, ``agents.read.all`` and ``workflows.read.all`` land on
permissions a role may already have held, so they are not restored. No
built-in role holds them.

Migrations never import live application code; the names below are frozen.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

GRAPH_RENAMES: dict[str, str] = {
    "apps.deploy.execute": "apps.publish",
    "solutions.deploy.execute": "solutions.deploy",
    "solutions.build.execute": "solutions.build",
    "users.lifecycle.readwrite": "userlifecycle.readwrite",
}
BASIC_RESOURCES: tuple[str, ...] = ("agents", "apps", "executions", "forms", "mcp", "settings")
# Old management-detail reads whose full view is now the plain read.
DETAIL_READS: tuple[str, ...] = ("apps", "forms", "mcp", "settings")
# Old management-detail reads that merge into a permission a role may
# already hold, so the downgrade cannot restore them.
MERGED_READS: dict[str, tuple[str, ...]] = {
    "reports.read.all": ("reports.read",),
    "agents.read.all": ("roles.read",),
    "workflows.read.all": ("roles.read", "settings.read"),
}

# The built-in roles' fixed ids (shared/builtin_roles.py), and the launch
# permissions each gains.
USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"
PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000007"
ADDED_LAUNCH_PERMISSIONS: dict[str, tuple[str, ...]] = {
    USER_ROLE_ID: ("agents.execute", "ai.execute", "ai.read", "workflows.execute"),
    PLATFORM_OPERATOR_ROLE_ID: ("agents.execute",),
}

revision: str = "20261009_graph_permission_names"
down_revision: Union[str, None] = "20261009_merge_integ_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _move(old: str, new: tuple[str, ...]) -> None:
    """Give every role holding ``old`` each of ``new`` instead."""
    connection = op.get_bind()
    for permission in new:
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) "
                "SELECT role_id, :new FROM role_permissions WHERE permission = :old "
                "ON CONFLICT DO NOTHING"
            ),
            {"old": old, "new": permission},
        )
    connection.execute(
        sa.text("DELETE FROM role_permissions WHERE permission = :old"),
        {"old": old},
    )


def _rename_grants(old: str, new: str) -> None:
    """Rename a permission held by workflow permission grants. The new name
    was invalid before this revision, so no grant can collide with it."""
    op.get_bind().execute(
        sa.text("UPDATE workflow_permission_grants SET permission = :new WHERE permission = :old"),
        {"old": old, "new": new},
    )


def upgrade() -> None:
    for old, new in sorted(GRAPH_RENAMES.items()):
        _move(old, (new,))
    for resource in BASIC_RESOURCES:
        _move(f"{resource}.read", (f"{resource}.readbasic",))
    for resource in DETAIL_READS:
        _move(f"{resource}.read.all", (f"{resource}.read",))
    for old, new in sorted(MERGED_READS.items()):
        _move(old, new)
    connection = op.get_bind()
    for role_id, permissions in sorted(ADDED_LAUNCH_PERMISSIONS.items()):
        for permission in permissions:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission) "
                    "VALUES (CAST(:role_id AS uuid), :permission) ON CONFLICT DO NOTHING"
                ),
                {"role_id": role_id, "permission": permission},
            )
    for old, new in sorted(GRAPH_RENAMES.items()):
        _rename_grants(old, new)


def downgrade() -> None:
    for old, new in sorted(GRAPH_RENAMES.items()):
        _rename_grants(new, old)
    connection = op.get_bind()
    for role_id, permissions in sorted(ADDED_LAUNCH_PERMISSIONS.items()):
        connection.execute(
            sa.text(
                "DELETE FROM role_permissions "
                "WHERE role_id = CAST(:role_id AS uuid) AND permission = ANY(:permissions)"
            ),
            {"role_id": role_id, "permissions": list(permissions)},
        )
    for resource in DETAIL_READS:
        _move(f"{resource}.read", (f"{resource}.read.all",))
    for resource in BASIC_RESOURCES:
        _move(f"{resource}.readbasic", (f"{resource}.read",))
    for old, new in sorted(GRAPH_RENAMES.items()):
        _move(new, (old,))
