"""
Git-connected Solution auto-pull (success-criteria §3.9, criterion 13).

A git-connected install has exactly one writer: auto-pull from its repo. The
platform clones/pulls the connected repo's ``main`` and deploys the workspace
found there via :class:`SolutionDeployer`. ``bifrost deploy`` and the REST deploy
endpoint are refused for a connected install (enforced in the deploy router), so
the one-writer invariant holds.

This module deliberately does NOT touch ``_repo/``: a connected Solution is its
own world, cloned to a throwaway checkout and deployed straight to
``_solutions/{id}/``. It reuses the same workspace layout the CLI reads
(``workflows/`` + ``modules/`` + ``shared/`` Python, ``.bifrost/*.yaml`` manifest).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer

logger = logging.getLogger(__name__)

# Top-level dirs whose .py files install as solution source (mirror the CLI).
_PY_SOURCE_DIRS = ("workflows", "modules", "shared")


def _collect_python_files(workspace: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for d in _PY_SOURCE_DIRS:
        root = workspace / d
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            files[py.relative_to(workspace).as_posix()] = py.read_text(encoding="utf-8")
    return files


def _collect_entities(workspace: Path, manifest_file: str, key: str) -> list[dict[str, Any]]:
    path = workspace / ".bifrost" / manifest_file
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    out: list[dict[str, Any]] = []
    for map_key, body in (data.get(key, {}) or {}).items():
        if isinstance(body, dict):
            out.append({**body, "id": body.get("id", map_key)})
    return out


def read_workspace_bundle(solution: Solution, workspace: Path) -> SolutionBundle:
    """Build a SolutionBundle from a checked-out Solution workspace dir."""
    workflows = _collect_entities(workspace, "workflows.yaml", "workflows")
    # Normalize workflow name (manifest is keyed by UUID; name is in the body).
    for wf in workflows:
        wf.setdefault("name", wf["id"])
    tables = _collect_entities(workspace, "tables.yaml", "tables")
    return SolutionBundle(
        solution=solution,
        python_files=_collect_python_files(workspace),
        workflows=workflows,
        tables=tables,
    )


async def deploy_from_workspace(
    db: AsyncSession, solution: Solution, workspace: Path
) -> None:
    """Deploy a connected install from an already-checked-out workspace dir.

    This is the testable core of auto-pull (no git): read the workspace, run the
    full-replace deploy. ``sync`` wraps this with the clone.
    """
    bundle = read_workspace_bundle(solution, workspace)
    await SolutionDeployer(db).deploy(bundle)


async def sync(db: AsyncSession, solution: Solution) -> None:
    """Clone the connected install's repo main and deploy the workspace.

    Called by the auto-pull trigger (webhook/poll) on a new commit to main.
    """
    if not solution.git_connected or not solution.git_repo_url:
        raise ValueError("sync() requires a git-connected solution with a repo url")

    from git import Repo as GitRepo  # GitPython (already a dep)

    with tempfile.TemporaryDirectory(prefix=f"bifrost-solution-{solution.slug}-") as tmp:
        work_dir = Path(tmp)
        GitRepo.clone_from(solution.git_repo_url, str(work_dir), branch="main", depth=1)
        logger.info("Cloned connected solution %s from %s", solution.id, solution.git_repo_url)
        await deploy_from_workspace(db, solution, work_dir)
