"""CLI entity-mutation command package.

Each module in this package defines a Click sub-group (``bifrost orgs ...``,
``bifrost roles ...``, etc.) that issues dedicated commands for mutating a
specific entity type. The command surface and MCP parity tool surface are
peers — both generate their parameter shape from the DTO-driven helpers in
:mod:`bifrost.dto_flags` and call :class:`bifrost.refs.RefResolver` for
name-to-UUID lookups.

The top-level ``bifrost`` CLI dispatches to these sub-groups via
:func:`dispatch_entity_subgroup`, called from ``bifrost.cli.main``.
"""

from __future__ import annotations

import sys

import click

from .agents import agents_group
from .apps import apps_group
from .claims import claims_group
from .configs import configs_group
from .events import events_group
from .files import files_group
from .forms import forms_group
from .integrations import integrations_group
from .orgs import orgs_group
from .requirements import requirements_group
from .roles import roles_group
from .tables import tables_group
from .workflows import workflows_group

# Map first-arg subcommand name to Click group. Argparse-style ``main`` in
# ``bifrost.cli`` consults this table and hands off ``args[1:]`` to the group.
ENTITY_GROUPS: dict[str, click.Group] = {
    "orgs": orgs_group,
    "roles": roles_group,
    "workflows": workflows_group,
    "forms": forms_group,
    "agents": agents_group,
    "apps": apps_group,
    "claims": claims_group,
    "integrations": integrations_group,
    "configs": configs_group,
    "tables": tables_group,
    "events": events_group,
    "files": files_group,
    "requirements": requirements_group,
}


def dispatch_entity_subgroup(name: str, args: list[str]) -> int:
    """Dispatch a single entity subgroup with the given residual args.

    Args:
        name: The entity subgroup name (matches a key of :data:`ENTITY_GROUPS`).
        args: Arguments after the subgroup name (e.g. ``["list"]``).

    Returns:
        Process exit code (Click's ``SystemExit.code``), or ``1`` on unknown
        subgroup.
    """
    group = ENTITY_GROUPS.get(name)
    if group is None:
        print(f"Unknown entity subgroup: {name}", file=sys.stderr)
        return 1
    try:
        group.main(args=args, standalone_mode=False, prog_name=f"bifrost {name}")
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.UsageError as exc:
        exc.show()
        return exc.exit_code


__all__ = ["ENTITY_GROUPS", "dispatch_entity_subgroup"]
