"""Agent skill-bundle asset reader.

This system tool is never selected from ``Agent.system_tools``.  The execution
planner injects it only for agents with ``bundle_path`` set, and dispatch passes
that exact bundle root through :class:`MCPContext`.
"""

from __future__ import annotations

import base64
import os
from pathlib import PurePosixPath
from typing import Any

from fastmcp.tools import ToolResult

from src.services.mcp_server.tool_result import error_result, success_result

READ_SKILL_ASSET_TOOL_ID = "read_skill_asset"
HIDDEN_TOOL_IDS = frozenset({READ_SKILL_ASSET_TOOL_ID})
_VIRTUAL_STORAGE_ROOT = "/__bifrost_skill_bundles__"
_MAX_ASSET_BYTES = 1_048_576


class SkillAssetPathError(ValueError):
    """A bundle root or model-supplied asset path escaped its allowed scope."""


def _relative_parts(value: str, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise SkillAssetPathError(f"{label} must be a non-empty relative path")
    # Object-storage keys are POSIX paths. Reject backslashes rather than
    # allowing a path that is safe on Linux but a traversal on another platform.
    if "\\" in value:
        raise SkillAssetPathError(f"{label} contains a platform path separator")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise SkillAssetPathError(f"{label} must stay beneath the bundle root")
    return pure.parts


def resolve_skill_asset_key(bundle_path: str, asset_path: str) -> str:
    """Return the source-storage key for one asset beneath ``bundle_path``.

    This deliberately mirrors ``WorkspaceRoot._resolve``: canonicalize the
    parent and enforce the CodeQL-recognized ``realpath`` + ``startswith``
    containment barrier. Object storage has no symlinks, so the canonical
    virtual root is sufficient while keeping the exact security invariant used
    by the builder workspace tools.
    """

    bundle_parts = _relative_parts(bundle_path, label="bundle_path")
    asset_parts = _relative_parts(asset_path, label="path")
    bundle_root = os.path.realpath(
        os.path.join(_VIRTUAL_STORAGE_ROOT, *bundle_parts)
    )
    candidate = os.path.realpath(os.path.join(bundle_root, *asset_parts))
    if not candidate.startswith(bundle_root + os.sep):
        raise SkillAssetPathError("path escapes the bundle root")
    return PurePosixPath(os.path.relpath(candidate, _VIRTUAL_STORAGE_ROOT)).as_posix()


async def read_skill_asset(context: Any, path: str) -> ToolResult:
    """Read one file beneath the executing agent's configured skill bundle."""

    bundle_path = getattr(context, "agent_bundle_path", None)
    if not bundle_path:
        return error_result("read_skill_asset is unavailable without an agent bundle")

    try:
        storage_path = resolve_skill_asset_key(bundle_path, path)
    except SkillAssetPathError as exc:
        return error_result(str(exc))

    try:
        builder_workspace = getattr(context, "builder_workspace", None)
        if builder_workspace is not None:
            from src.services.builder.fs_tools import (
                WorkspaceRoot,
                WorkspaceViolation,
            )

            if not isinstance(builder_workspace, WorkspaceRoot):
                return error_result("builder workspace has an invalid execution scope")
            try:
                content, truncated = builder_workspace.read_file(storage_path)
            except WorkspaceViolation as exc:
                return error_result(str(exc))
            if truncated:
                return error_result(
                    f"skill asset exceeds the {_MAX_ASSET_BYTES}-byte read limit"
                )
        elif solution_id := getattr(context, "agent_solution_id", None):
            from src.services.solutions.storage import SolutionStorage

            content = await SolutionStorage(solution_id).read(storage_path)
        elif getattr(context, "agent_skill_in_repo", False):
            from src.services.repo_storage import RepoStorage

            content = await RepoStorage().read(storage_path)
        else:
            from src.services.agent_skill_storage import AgentSkillStorage

            agent_id = getattr(context, "agent_skill_id", None)
            if not agent_id:
                return error_result(
                    "read_skill_asset is unavailable without an agent storage scope"
                )
            content = await AgentSkillStorage(agent_id).read(storage_path)
    except Exception as exc:
        # Storage clients use provider-specific not-found exceptions. Do not
        # leak bucket/key internals through the model-facing tool result.
        return error_result(f"skill asset could not be read: {type(exc).__name__}")

    if len(content) > _MAX_ASSET_BYTES:
        return error_result(
            f"skill asset exceeds the {_MAX_ASSET_BYTES}-byte read limit"
        )

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return success_result(
            f"Read binary skill asset: {path}",
            {
                "path": path,
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
            },
        )
    return success_result(
        f"Read skill asset: {path}",
        {"path": path, "encoding": "utf-8", "content": text},
    )


TOOLS = [
    (
        READ_SKILL_ASSET_TOOL_ID,
        "Read Skill Asset",
        "Read a relative file from the current agent's portable skill bundle.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register the bundle reader for authenticated MCP dispatch."""

    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    register_tool_with_context(
        mcp,
        read_skill_asset,
        READ_SKILL_ASSET_TOOL_ID,
        TOOLS[0][2],
        get_context_fn,
    )
