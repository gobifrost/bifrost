"""Deterministic inventory of Bifrost's callable product surfaces."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import click
from fastapi import FastAPI
from fastapi.routing import APIRoute

from bifrost.commands import ENTITY_GROUPS
from bifrost.commands.solution import solution_group
from bifrost.manifest import Manifest
from src.models.contracts.operation_catalog import OperationSurfaceStatus
from src.services.builder.mcp_harness import BUILDER_TOOL_IDS
from src.services.mcp_server.server import get_system_tools
from src.services.operation_catalog import OPERATION_CATALOG


_CLI_LIFECYCLE_PATHS: tuple[tuple[str, ...], ...] = (
    ("api",),
    ("auth", "default"),
    ("auth", "list"),
    ("auth", "token"),
    ("auth", "use"),
    ("deploy",),
    ("git", "commit"),
    ("git", "diff"),
    ("git", "discard"),
    ("git", "fetch"),
    ("git", "push"),
    ("git", "resolve"),
    ("git", "status"),
    ("login",),
    ("logout",),
    ("migrate-imports",),
    ("pull",),
    ("push",),
    ("run",),
    ("skill", "list"),
    ("skill", "remove"),
    ("skill", "update"),
    ("sync",),
    ("update",),
    ("watch",),
)

_SDK_HTTP_RE = re.compile(r'^\s*"(?P<method>[A-Z]+) (?P<path>/api/[^" ]+)"')


def _click_leaf_paths(
    group: click.Group,
    prefix: tuple[str, ...],
) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    for name, command in sorted(group.commands.items()):
        path = (*prefix, name)
        if isinstance(command, click.Group):
            paths.extend(_click_leaf_paths(command, path))
        else:
            paths.append(path)
    return paths


def collect_rest_surface(app: FastAPI) -> list[dict[str, str]]:
    """Return every static HTTP method/path pair registered on the API."""

    rows: list[dict[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):  # one APIRoute may expose several
            if method in {"HEAD", "OPTIONS"}:
                continue
            rows.append(
                {
                    "method": method,
                    "path": route.path,
                    "operation_id": route.operation_id or "",
                }
            )
    return sorted(rows, key=lambda row: (row["path"], row["method"]))


def collect_cli_surface() -> list[dict[str, Any]]:
    """Return all resource/Solution leaves plus documented lifecycle leaves."""

    entity_paths = [
        path
        for group_name, group in sorted(ENTITY_GROUPS.items())
        for path in _click_leaf_paths(group, (group_name,))
    ]
    solution_paths = _click_leaf_paths(solution_group, ("solution",))
    rows = [
        {
            "path": list(path),
            "kind": "lifecycle" if path in _CLI_LIFECYCLE_PATHS else "operation",
        }
        for path in (*_CLI_LIFECYCLE_PATHS, *entity_paths, *solution_paths)
    ]
    unique = {tuple(row["path"]): row for row in rows}
    return [unique[path] for path in sorted(unique)]


def collect_mcp_surface() -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "name": str(tool["id"]),
                "hidden": bool(tool.get("hidden", False)),
            }
            for tool in get_system_tools()
        ),
        key=lambda row: row["name"],
    )


def collect_builder_surface() -> list[dict[str, str]]:
    return [{"name": name} for name in sorted(BUILDER_TOOL_IDS)]


def collect_manifest_surface() -> list[dict[str, str]]:
    return [{"entity": name} for name in sorted(Manifest.model_fields)]


def collect_sdk_surface(repo_root: Path) -> list[dict[str, str]]:
    """Read the v2 SDK's existing canonical wire-surface declaration."""

    relative = Path("client/src/lib/app-sdk/wire-surface.ts")
    candidates = (repo_root / relative, Path("/") / relative)
    source_path = next((path for path in candidates if path.is_file()), None)
    if source_path is None:
        raise FileNotFoundError("client/src/lib/app-sdk/wire-surface.ts is unavailable")
    source = source_path.read_text(encoding="utf-8")
    rows = [
        {"method": match.group("method"), "path": match.group("path")}
        for line in source.splitlines()
        if (match := _SDK_HTTP_RE.match(line))
    ]
    rows.append({"method": "WEBSOCKET", "path": "/ws/connect"})
    return sorted(rows, key=lambda row: (row["path"], row["method"]))


def build_operation_inventory(app: FastAPI, repo_root: Path) -> dict[str, Any]:
    """Compare observed surfaces to canonical operations and account for all."""

    surfaces = {
        "rest": collect_rest_surface(app),
        "cli": collect_cli_surface(),
        "mcp": collect_mcp_surface(),
        "native_builder": collect_builder_surface(),
        "manifest": collect_manifest_surface(),
        "sdk": collect_sdk_surface(repo_root),
    }
    rest_by_key = {
        (row["method"], row["path"]): row for row in surfaces["rest"]
    }
    cli_paths = {tuple(row["path"]) for row in surfaces["cli"]}
    mcp_names = {row["name"] for row in surfaces["mcp"]}
    builder_names = {row["name"] for row in surfaces["native_builder"]}
    manifest_entities = {row["entity"] for row in surfaces["manifest"]}
    sdk_keys = {(row["method"], row["path"]) for row in surfaces["sdk"]}

    catalog_rows: list[dict[str, Any]] = []
    consumed: dict[str, set[Any]] = {surface: set() for surface in surfaces}
    for operation in OPERATION_CATALOG:
        rest_key = (operation.rest.method, operation.rest.path)
        rest_observed = rest_by_key.get(rest_key)
        rest_exact = bool(
            rest_observed
            and rest_observed["operation_id"] == operation.operation_id
        )
        if rest_observed:
            consumed["rest"].add(rest_key)

        cli_path = operation.cli.path if operation.cli else None
        if cli_path in cli_paths:
            consumed["cli"].add(cli_path)

        mcp_name = operation.mcp.name if operation.mcp else None
        legacy_mcp_name = mcp_name.removeprefix("bifrost_") if mcp_name else None
        observed_mcp = (
            mcp_name
            if mcp_name in mcp_names
            else legacy_mcp_name if legacy_mcp_name in mcp_names else None
        )
        if observed_mcp:
            consumed["mcp"].add(observed_mcp)

        manifest_entity = operation.manifest.entity if operation.manifest else None
        if manifest_entity in manifest_entities:
            consumed["manifest"].add(manifest_entity)

        sdk_key = rest_key if rest_key in sdk_keys else None
        if sdk_key:
            consumed["sdk"].add(sdk_key)

        builder_name = mcp_name if mcp_name in builder_names else None
        if builder_name:
            consumed["native_builder"].add(builder_name)

        catalog_rows.append(
            {
                "operation": operation.model_dump(mode="json"),
                "observed": {
                    "rest": {
                        "status": (
                            OperationSurfaceStatus.EXACT
                            if rest_exact
                            else OperationSurfaceStatus.DIVERGENT
                            if rest_observed
                            else OperationSurfaceStatus.MISSING
                        ).value,
                        "operation_id": (
                            rest_observed["operation_id"] if rest_observed else None
                        ),
                    },
                    "cli": {
                        "status": (
                            OperationSurfaceStatus.EXACT
                            if cli_path in cli_paths
                            else OperationSurfaceStatus.MISSING
                        ).value,
                        "path": list(cli_path) if cli_path else None,
                    },
                    "mcp": {
                        "status": (
                            OperationSurfaceStatus.EXACT
                            if observed_mcp == mcp_name
                            else OperationSurfaceStatus.DIVERGENT
                            if observed_mcp
                            else OperationSurfaceStatus.MISSING
                        ).value,
                        "name": observed_mcp,
                    },
                    "native_builder": {
                        "status": (
                            OperationSurfaceStatus.EXACT
                            if builder_name
                            else OperationSurfaceStatus.MISSING
                        ).value,
                        "name": builder_name,
                    },
                    "manifest": {
                        "status": (
                            OperationSurfaceStatus.EXACT
                            if manifest_entity in manifest_entities
                            else OperationSurfaceStatus.INTENTIONALLY_UNSUPPORTED
                            if operation.exclusions.get("manifest")
                            else OperationSurfaceStatus.MISSING
                        ).value,
                        "entity": manifest_entity,
                    },
                    "sdk": {
                        "status": (
                            OperationSurfaceStatus.EXACT
                            if sdk_key
                            else OperationSurfaceStatus.INTENTIONALLY_UNSUPPORTED
                            if operation.exclusions.get("sdk")
                            else OperationSurfaceStatus.MISSING
                        ).value,
                        "binding": (
                            {"method": sdk_key[0], "path": sdk_key[1]}
                            if sdk_key
                            else None
                        ),
                    },
                },
            }
        )

    uncataloged = {
        "rest": [
            {
                **row,
                "status": OperationSurfaceStatus.MISSING.value,
                "reason": "REST operation has not entered the canonical operation catalog yet.",
            }
            for row in surfaces["rest"]
            if (row["method"], row["path"]) not in consumed["rest"]
        ],
        "cli": [
            {
                **row,
                "status": (
                    OperationSurfaceStatus.TRANSPORT_ONLY
                    if row["kind"] == "lifecycle"
                    else OperationSurfaceStatus.MISSING
                ).value,
                "reason": (
                    "Local authentication, source-control, or lifecycle command."
                    if row["kind"] == "lifecycle"
                    else "CLI operation has not entered the canonical operation catalog yet."
                ),
            }
            for row in surfaces["cli"]
            if tuple(row["path"]) not in consumed["cli"]
        ],
        "mcp": [
            {
                **row,
                "status": OperationSurfaceStatus.MISSING.value,
                "reason": "MCP tool has not entered the canonical operation catalog yet.",
            }
            for row in surfaces["mcp"]
            if row["name"] not in consumed["mcp"]
        ],
        "native_builder": [
            {
                **row,
                "status": OperationSurfaceStatus.TRANSPORT_ONLY.value,
                "reason": "Execution-scoped workspace primitive, not a platform entity operation.",
            }
            for row in surfaces["native_builder"]
            if row["name"] not in consumed["native_builder"]
        ],
        "manifest": [
            {
                **row,
                "status": OperationSurfaceStatus.MISSING.value,
                "reason": "Manifest entity has not entered the canonical operation catalog yet.",
            }
            for row in surfaces["manifest"]
            if row["entity"] not in consumed["manifest"]
        ],
        "sdk": [
            {
                **row,
                "status": OperationSurfaceStatus.TRANSPORT_ONLY.value,
                "reason": "Application runtime SDK binding; not an administration operation.",
            }
            for row in surfaces["sdk"]
            if (row["method"], row["path"]) not in consumed["sdk"]
        ],
    }
    return {
        "schema_version": 1,
        "counts": {
            surface: len(rows) for surface, rows in sorted(surfaces.items())
        },
        "catalog_operations": catalog_rows,
        "uncataloged": uncataloged,
    }


__all__ = [
    "build_operation_inventory",
    "collect_builder_surface",
    "collect_cli_surface",
    "collect_manifest_surface",
    "collect_mcp_surface",
    "collect_rest_surface",
    "collect_sdk_surface",
]
