"""CLI commands for managing the workspace's Python requirements.txt.

Maps to ``/api/packages/*`` on the platform:

* ``bifrost requirements list`` → ``GET /api/packages``
* ``bifrost requirements install [pkg[==ver]]`` → ``POST /api/packages/install``
  (no arg: warm the Redis cache from S3 + recycle workers; pkg arg: append/update
  the package in requirements.txt + recycle workers)
* ``bifrost requirements remove <pkg>`` → ``DELETE /api/packages/{pkg}``

Workers recycle their process pools after install/remove; new processes pip
install from requirements.txt on startup. The CLI returns immediately —
recycling happens asynchronously on the platform side.
"""

from __future__ import annotations

import click

from bifrost.client import BifrostClient
from bifrost.refs import RefResolver

from .base import entity_group, output_result, pass_resolver, run_async


requirements_group = entity_group(
    "requirements",
    "Manage the workspace's Python requirements.txt (workers auto-recycle).",
)


def _split_spec(spec: str) -> tuple[str, str | None]:
    """Split ``pkg`` or ``pkg==1.2`` into (name, version|None)."""
    if "==" in spec:
        name, version = spec.split("==", 1)
        return name.strip(), version.strip() or None
    return spec.strip(), None


@requirements_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_requirements(
    ctx: click.Context,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """List installed Python packages on the platform."""
    response = await client.get("/api/packages")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@requirements_group.command("install")
@click.argument("spec", required=False)
@click.pass_context
@pass_resolver
@run_async
async def install_requirements(
    ctx: click.Context,
    spec: str | None,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Install a package or recycle workers from current requirements.txt.

    Examples:

      bifrost requirements install                  # warm cache + recycle workers
      bifrost requirements install reportlab        # append, then recycle
      bifrost requirements install httpx==0.27.0    # pin version, then recycle
    """
    body: dict = {}
    if spec:
        name, version = _split_spec(spec)
        body["package_name"] = name
        if version:
            body["version"] = version

    response = await client.post("/api/packages/install", json=body)
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


@requirements_group.command("remove")
@click.argument("package_name")
@click.pass_context
@pass_resolver
@run_async
async def remove_requirement(
    ctx: click.Context,
    package_name: str,
    *,
    client: BifrostClient,
    resolver: RefResolver,  # noqa: ARG001 - kept for signature parity
) -> None:
    """Remove a package from requirements.txt and recycle workers."""
    response = await client.delete(f"/api/packages/{package_name}")
    response.raise_for_status()
    output_result(response.json(), ctx=ctx)


__all__ = ["requirements_group"]
