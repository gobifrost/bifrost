"""CLI commands for asking what a user's access is, and for changing their roles.

* ``bifrost users list [--identities]`` → ``GET /api/users``
* ``bifrost users create --identity`` → ``POST /api/identities``
* ``bifrost users access <user>`` → ``GET /api/users/{id}/access``
* ``bifrost users access check <user>`` → ``POST /api/users/{id}/access/check``
* ``bifrost users roles get <user>`` → ``GET /api/users/{id}/role-assignments``
* ``bifrost users roles set <user>`` → ``PUT /api/users/{id}/role-assignments``

``list`` shows people, or with ``--identities`` the identities that run work no person
started; ``create --identity`` adds a custom one. ``access`` shows what the user can do and where. ``access check`` is a what-if:
could this user perform an operation in an organization, directly or through a
workflow? The answer is the access model's trace; nothing is enforced.
"""

from __future__ import annotations

from typing import Any

import click

from bifrost.client import BifrostClient
from bifrost.org_target import resolve_org_target
from bifrost.refs import RefResolver

from .base import EntityGroup, entity_group, output_result, pass_resolver, run_async
from .permissions import SCOPE_LABELS, permission_names

users_group = entity_group("users", "Show and change what a user can do, and ask what their access would be.")

_IDENTITY_KINDS = {"org_default": "Default", "global_default": "Global", "custom": "Custom"}

_MARKS = {"passed": "✓", "stopped": "✗", "not_applicable": "–", "not_reached": "·"}


def _print_users(users: list[dict[str, Any]]) -> None:
    for user in users:
        kind = _IDENTITY_KINDS.get(user.get("identity_kind"))
        click.echo(f"{user['name']}  {kind or user['email']}")


def _print_created_identity(identity: dict[str, Any]) -> None:
    kind = _IDENTITY_KINDS[identity["identity_kind"]]
    place = identity["organization_name"] or "Global"
    click.echo(f"Created {identity['name']} ({kind}, {place})  {identity['id']}")


def _print_trace(trace: dict[str, Any]) -> None:
    for step in trace["steps"]:
        click.echo(f"{_MARKS[step['status']]}  {step['label']}  {step['reason']}".rstrip())
    note = "" if trace["enforced"] else " (not enforced)"
    click.echo(f"Outcome: {trace['outcome']}{note}")


def _print_access_map(access_map: dict[str, Any], names: dict[str, str]) -> None:
    click.echo("Reach: " + ", ".join(place["label"] for place in access_map["reach"]))
    for row in access_map["rows"]:
        click.echo()
        click.echo(row["place"]["label"])
        for grant in row["grants"]:
            permission = grant["permission"]
            sources = "; ".join(f"{source['role_name']}, {source['via']}" for source in grant["sources"])
            name = f"{names[permission]}  " if permission in names else ""
            click.echo(f"  {name}{permission}  {SCOPE_LABELS[grant['scope']]}  ({sources})")


@users_group.command("list")
@click.option("--identities", is_flag=True, help="List identities instead of people.")
@click.pass_context
@pass_resolver
@run_async
async def list_users(
    ctx: click.Context,
    identities: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List the people you may read, or with --identities the identities.

    Identities are the accounts that run work no person started: each organization's default, the global default, and custom ones.
    """
    response = await client.get("/api/users", params={"identities": "only"} if identities else None)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_users)


@users_group.command("create")
@click.option("--identity", "identity", is_flag=True, required=True, help="Create an identity (the only thing created here).")
@click.option("--org", required=True, help="Organization UUID or name, or 'global'.")
@click.option("--name", required=True, help="The identity's name.")
@click.pass_context
@pass_resolver
@run_async
async def create_identity(
    ctx: click.Context,
    identity: bool,  # noqa: ARG001 - required to be set; selects what to create
    org: str,
    name: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Create a custom identity in --org, or Global.

    The identity starts with the User base role and no additional roles; give it roles with `bifrost users roles set`, then point a workflow at it with `bifrost workflows update --run-as`.

    Example:

      bifrost users create --identity --org Contoso --name "Contoso Nightly"
    """
    target = await resolve_org_target(org, False, resolver)
    response = await client.post("/api/identities", json={"name": name, "organization_id": target.organization_id})
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_created_identity)


class _AccessGroup(EntityGroup):
    """``users access`` runs ``show`` when its first argument isn't a subcommand."""

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if args and not args[0].startswith("-") and self.get_command(ctx, args[0]) is None:
            return "show", self.commands["show"], args
        return super().resolve_command(ctx, args)


@click.group(name="access", cls=_AccessGroup, help="Show a user's access, or check what it would allow.")
@click.pass_context
def access_group(ctx: click.Context) -> None:
    ctx.ensure_object(dict)


users_group.add_command(access_group)


@access_group.command("show")
@click.argument("user_ref", metavar="USER")
@click.pass_context
@pass_resolver
@run_async
async def show_access(
    ctx: click.Context,
    user_ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Show what USER can do, and where.

    Lists every place the user reaches and the permissions held there. USER is a UUID or an email. `bifrost users access USER` is the same as `bifrost users access show USER`.

    Example:

      bifrost users access ada@contoso.test
    """
    user_id = await resolver.resolve("user", user_ref)
    response = await client.get(f"/api/users/{user_id}/access")
    response.raise_for_status()
    catalog = await client.get("/api/permissions/catalog")
    catalog.raise_for_status()
    names = permission_names(catalog.json())
    output_result(response.json(), ctx=ctx, human=lambda access_map: _print_access_map(access_map, names))


@access_group.command("check")
@click.argument("user_ref", metavar="USER")
@click.option("--org", required=True, help="Organization UUID or name, or 'global'.")
@click.option("--operation", required=True, help="Catalog operation id or 'METHOD /api/path'.")
@click.option("--workflow", help="Workflow whose powers apply (UUID, name, or path::func). Omit to act directly.")
@click.pass_context
@pass_resolver
@run_async
async def check_access(
    ctx: click.Context,
    user_ref: str,
    org: str,
    operation: str,
    workflow: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Show how the access model decides USER performing --operation in --org.

    USER is a UUID or an email.

    Examples:

    \b
      bifrost users access check ada@contoso.test --org global --operation tables.documents.create
      bifrost users access check ada@contoso.test --org Contoso --operation tables.documents.create --workflow "Sync Invoices"
    """
    user_id = await resolver.resolve("user", user_ref)
    body: dict[str, Any] = {
        "organization_id": "global" if org == "global" else await resolver.resolve("org", org),
        "operation": operation,
    }
    if workflow is not None:
        body["workflow_id"] = await resolver.resolve("workflow", workflow)

    response = await client.post(f"/api/users/{user_id}/access/check", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_trace)


_BOUNDARY_PLACES = {
    "customers": {"kind": "managed_organizations"},
    "global": {"kind": "platform"},
}

roles_group = entity_group("roles", "Show and replace a user's roles.")
users_group.add_command(roles_group)


def _print_role_assignments(assignments: dict[str, Any]) -> None:
    click.echo(f"Base role: {assignments['base_role']['name']}")
    for role in assignments["additional"]:
        places = ", ".join(_boundary_label(boundary) for boundary in role["boundaries"])
        click.echo(f"{role['name']}  {places}")


async def _current_assignments(client: BifrostClient, url: str) -> dict[str, Any]:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


def _boundary_input(boundary: dict[str, Any]) -> dict[str, Any]:
    if boundary["kind"] == "organization":
        return {"kind": "organization", "organization_id": boundary["organization_id"]}
    return {"kind": boundary["kind"]}


def _boundary_label(boundary: dict[str, Any]) -> str:
    if boundary["kind"] == "organization":
        return boundary["organization_name"]
    if boundary["kind"] == "managed_organizations":
        return "All Customer Organizations"
    return "Global"


async def _resolve_places(
    places: str, *, client: BifrostClient, resolver: RefResolver
) -> list[dict[str, Any]]:
    """Turn a comma list of ``org:<ref>``, ``customers``, ``global`` and ``all`` into boundaries."""
    boundaries: list[dict[str, Any]] = []
    for token in (token.strip() for token in places.split(",")):
        if token.startswith("org:") and token != "org:":
            boundaries.append({"kind": "organization", "organization_id": await resolver.resolve("org", token[4:])})
        elif token in _BOUNDARY_PLACES:
            boundaries.append(_BOUNDARY_PLACES[token])
        elif token == "all":
            response = await client.get("/auth/authorization")
            response.raise_for_status()
            boundaries += [
                _BOUNDARY_PLACES["customers"],
                {"kind": "organization", "organization_id": response.json()["provider_organization_id"]},
                _BOUNDARY_PLACES["global"],
            ]
        else:
            raise click.UsageError(f"Unknown place {token!r}: use org:<organization>, customers, global or all.")
    return [dict(boundary) for boundary in {tuple(boundary.items()): boundary for boundary in boundaries}.values()]


@roles_group.command("get")
@click.argument("user_ref", metavar="USER")
@click.pass_context
@pass_resolver
@run_async
async def get_roles(
    ctx: click.Context,
    user_ref: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Show USER's base role and additional roles, with the places each applies.

    USER is a UUID or an email.
    """
    user_id = await resolver.resolve("user", user_ref)
    response = await client.get(f"/api/users/{user_id}/role-assignments")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_role_assignments)


@roles_group.command("set")
@click.argument("user_ref", metavar="USER")
@click.option("--base", "base_ref", help="Base role (UUID or name). Omit to keep the current one.")
@click.option(
    "--role",
    "role_specs",
    multiple=True,
    metavar="ROLE[=PLACES]",
    help=(
        "Additional role, repeatable. PLACES is a comma list of org:<organization>, customers, global or all; "
        "omit it for the role's default (the user's home organization)."
    ),
)
@click.option("--no-roles", is_flag=True, help="Remove every additional role.")
@click.pass_context
@pass_resolver
@run_async
async def set_roles(
    ctx: click.Context,
    user_ref: str,
    base_ref: str | None,
    role_specs: tuple[str, ...],
    no_roles: bool,
    *,
    client: BifrostClient,
    resolver: RefResolver,
) -> None:
    """Replace USER's additional roles, and optionally their base role.

    The --role options replace the additional roles; --no-roles removes them all. With only --base, the
    additional roles stay as they are.

    Examples:

    \b
      bifrost users roles set ada@contoso.test --role Helpdesk=org:Contoso,org:Fabrikam
      bifrost users roles set ada@contoso.test --base User --role Auditor=all
      bifrost users roles set ada@contoso.test --no-roles
    """
    if no_roles and role_specs:
        raise click.UsageError("--no-roles cannot be combined with --role.")
    if not (no_roles or role_specs or base_ref):
        raise click.UsageError("Nothing to change: give --base, --role or --no-roles.")

    user_id = await resolver.resolve("user", user_ref)
    base_role_id = await resolver.resolve("role", base_ref) if base_ref else None
    additional: list[dict[str, Any]] = []
    for spec in role_specs:
        role_ref, has_places, places = spec.partition("=")
        entry: dict[str, Any] = {"role_id": await resolver.resolve("role", role_ref)}
        if has_places:
            entry["boundaries"] = await _resolve_places(places, client=client, resolver=resolver)
        additional.append(entry)

    url = f"/api/users/{user_id}/role-assignments"
    if no_roles or role_specs:
        if base_role_id is None:
            base_role_id = (await _current_assignments(client, url))["base_role"]["id"]
    else:
        assignments = await _current_assignments(client, url)
        base_role_id = base_role_id or assignments["base_role"]["id"]
        additional = [
            {"role_id": role["role_id"], "boundaries": [_boundary_input(b) for b in role["boundaries"]]}
            for role in assignments["additional"]
        ]

    response = await client.put(url, json={"base_role_id": base_role_id, "additional": additional})
    response.raise_for_status()
    output_result(response.json(), ctx=ctx, human=_print_role_assignments)


__all__ = ["users_group"]
