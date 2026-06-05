"""CLI command ``bifrost solution`` (and the top-level ``bifrost deploy``).

A Solution is an installable surface (success-criteria §3). These commands are
the disconnected-install writer and are **non-interactive by contract**:
``deploy`` always applies the full bundle, so the whole create → deploy → run
loop runs headless (criterion 17).

* ``bifrost solution init`` — scaffold a ``bifrost.solution.yaml`` descriptor.
* ``bifrost solution deploy`` (alias: top-level ``bifrost deploy``) — read the
  descriptor, ensure the install exists, bundle the workspace's Python source +
  workflow manifest entries, and POST to ``/api/solutions/{id}/deploy``.

Apps/forms/agents/tables bundling joins in their sub-plans; Sub-plan 1 wires the
load-bearing workflow path.
"""

from __future__ import annotations

import asyncio
import os
import pathlib

import click
import yaml

from bifrost.client import BifrostClient
from bifrost.solution_descriptor import (
    DESCRIPTOR_FILENAME,
    is_solution_workspace,
    load_descriptor,
)

# Top-level source dirs whose .py files are installed as solution source.
_PY_SOURCE_DIRS = ("workflows", "modules", "shared")


def _noninteractive(yes: bool) -> bool:
    """deploy never prompts; this is here for parity with the sync path."""
    return yes or os.environ.get("BIFROST_NONINTERACTIVE") == "1"


@click.group(name="solution", help="Manage Solution installs (installable surfaces).")
def solution_group() -> None:
    pass


@solution_group.command(name="init", help="Scaffold a bifrost.solution.yaml descriptor.")
@click.argument("path", type=click.Path(file_okay=False), default=".")
@click.option("--slug", required=True, help="Solution slug (definition identity).")
@click.option("--name", default=None, help="Display name (defaults to slug).")
@click.option("--scope", type=click.Choice(["org", "global"]), default="org", show_default=True)
@click.option("--global-repo-access/--no-global-repo-access", default=False, show_default=True)
def init_cmd(path: str, slug: str, name: str | None, scope: str, global_repo_access: bool) -> None:
    workspace = pathlib.Path(path)
    workspace.mkdir(parents=True, exist_ok=True)
    descriptor = workspace / DESCRIPTOR_FILENAME
    if descriptor.exists():
        raise click.ClickException(f"{descriptor} already exists")
    descriptor.write_text(
        yaml.safe_dump(
            {
                "slug": slug,
                "name": name or slug,
                "scope": scope,
                "global_repo_access": global_repo_access,
            },
            sort_keys=False,
        )
    )
    click.echo(f"Wrote {descriptor}")


def _collect_python_files(workspace: pathlib.Path) -> dict[str, str]:
    """Collect installable Python source (relative path → text)."""
    files: dict[str, str] = {}
    for d in _PY_SOURCE_DIRS:
        root = workspace / d
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            rel = py.relative_to(workspace).as_posix()
            files[rel] = py.read_text(encoding="utf-8")
    return files


def _collect_workflows(workspace: pathlib.Path) -> list[dict]:
    """Read workflow entries from .bifrost/workflows.yaml (the descriptor indexes it)."""
    wf_file = workspace / ".bifrost" / "workflows.yaml"
    if not wf_file.is_file():
        return []
    data = yaml.safe_load(wf_file.read_text()) or {}
    raw = data.get("workflows", {})
    entries: list[dict] = []
    # workflows.yaml is keyed by workflow UUID; the display name is body["name"].
    for key, body in raw.items():
        if not isinstance(body, dict):
            continue
        entries.append({
            "id": body.get("id", key),
            "name": body.get("name") or key,
            "function_name": body["function_name"],
            "path": body["path"],
            "type": body.get("type", "workflow"),
            "description": body.get("description"),
            "access_level": body.get("access_level"),
        })
    return entries


def _collect_tables(workspace: pathlib.Path) -> list[dict]:
    """Read table SCHEMA/POLICIES from .bifrost/tables.yaml (keyed by UUID).

    Only structure is deployed — row data is runtime state and never carried in
    a bundle (criterion 11).
    """
    tbl_file = workspace / ".bifrost" / "tables.yaml"
    if not tbl_file.is_file():
        return []
    data = yaml.safe_load(tbl_file.read_text()) or {}
    raw = data.get("tables", {})
    entries: list[dict] = []
    for key, body in raw.items():
        if not isinstance(body, dict):
            continue
        entry = {
            "id": body.get("id", key),
            "name": body.get("name") or key,
            "description": body.get("description"),
            "schema": body.get("schema"),
        }
        if "policies" in body:
            entry["policies"] = body["policies"]
        entries.append(entry)
    return entries


@solution_group.command(name="deploy", help="Deploy the current Solution workspace (full replace, non-interactive).")
@click.argument("path", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--solution", "solution_id", default=None, help="Target install id (override when ambiguous).")
@click.option("--yes", "-y", is_flag=True, default=False, help="Non-interactive: apply the full bundle without prompting.")
def deploy_cmd(path: str, solution_id: str | None, yes: bool) -> None:
    workspace = pathlib.Path(path).resolve()
    if not is_solution_workspace(workspace):
        raise click.ClickException(
            f"No {DESCRIPTOR_FILENAME} in {workspace} — not a Solution workspace. "
            f"Run `bifrost solution init` first."
        )
    descriptor = load_descriptor(workspace)
    _noninteractive(yes)  # deploy is always full-replace; flag kept for contract parity

    python_files = _collect_python_files(workspace)
    workflows = _collect_workflows(workspace)
    tables = _collect_tables(workspace)

    async def _run() -> int:
        client = BifrostClient.get_instance(require_auth=True)

        target_id = solution_id
        if target_id is None:
            # Resolve or create the install by (slug, scope).
            resp = await client.get("/api/solutions")
            if resp.status_code == 200:
                for s in resp.json().get("solutions", []):
                    same_scope = (
                        (descriptor.scope == "global" and s.get("organization_id") is None)
                        or (descriptor.scope == "org" and s.get("organization_id") is not None)
                    )
                    if s.get("slug") == descriptor.slug and same_scope:
                        target_id = s["id"]
                        break
            if target_id is None:
                create = await client.post("/api/solutions", json={
                    "slug": descriptor.slug,
                    "name": descriptor.name,
                    "scope": descriptor.scope,
                    "global_repo_access": descriptor.global_repo_access,
                    "git_connected": descriptor.git_connected,
                    "git_repo_url": descriptor.git_repo_url,
                })
                if create.status_code not in (200, 201):
                    click.echo(f"Failed to create install: {create.status_code} {create.text}", err=True)
                    return 1
                target_id = create.json()["id"]

        deploy = await client.post(f"/api/solutions/{target_id}/deploy", json={
            "python_files": python_files,
            "workflows": workflows,
            "tables": tables,
        })
        if deploy.status_code not in (200, 201):
            click.echo(f"Deploy failed: {deploy.status_code} {deploy.text}", err=True)
            return 1
        body = deploy.json()
        click.echo(
            f"Deployed install {target_id}: "
            f"{body.get('workflows_upserted', 0)} workflow(s) upserted, "
            f"{body.get('workflows_deleted', 0)} deleted."
        )
        return 0

    rc = asyncio.run(_run())
    if rc:
        raise SystemExit(rc)


def handle_solution(args: list[str]) -> int:
    """Dispatch ``bifrost solution ...`` from :func:`bifrost.cli.main`."""
    try:
        solution_group.main(args=args, standalone_mode=False, prog_name="bifrost solution")
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.UsageError as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


def handle_deploy(args: list[str]) -> int:
    """Dispatch the top-level ``bifrost deploy`` (alias of ``solution deploy``)."""
    try:
        deploy_cmd.main(args=args, standalone_mode=False, prog_name="bifrost deploy")
        return 0
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.UsageError as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


__all__ = ["solution_group", "handle_solution", "handle_deploy"]
