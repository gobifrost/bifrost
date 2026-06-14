"""
Git-connected Solution auto-pull (success-criteria §3.9, criterion 13).

A git-connected install has exactly one writer: auto-pull from its repo. The
platform clones/pulls the connected repo's ``main`` and deploys the workspace
found there via :class:`SolutionDeployer`. ``bifrost deploy`` and the REST deploy
endpoint are refused for a connected install (enforced in the deploy router), so
the one-writer invariant holds.

This module deliberately does NOT touch ``_repo/``: a connected Solution is its
own world, cloned to a throwaway checkout and deployed straight to
``_solutions/{id}/``. It reuses the CLI's own collectors (layout-agnostic Python
source from any folder, ``.bifrost/*.yaml`` manifest) so a git-connected deploy
bundles exactly what ``bifrost deploy`` would.
"""

from __future__ import annotations

import logging
import asyncio
import tempfile
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import (
    DeployResult,
    SolutionBundle,
    SolutionDeployer,
    SolutionFinalizeIncomplete,
)

logger = logging.getLogger(__name__)

# Root descriptor that marks a Solution workspace (must match
# bifrost.solution_descriptor.DESCRIPTOR_FILENAME).
_DESCRIPTOR_FILENAME = "bifrost.solution.yaml"


class NotASolutionWorkspace(Exception):
    """The checkout has no bifrost.solution.yaml — refuse the full-replace sync."""


def _collect_python_files(workspace: Path) -> dict[str, str]:
    # Reuse the CLI's canonical, layout-agnostic collector so the git-connected
    # deploy path bundles the SAME files as `bifrost deploy` — a divergent
    # allow-list here is exactly what dropped functions/*.py from CLI deploys
    # (shakeout HIGH). git_sync already imports _collect_apps from the same module.
    from bifrost.commands.solution import _collect_python_files as _cli_collect

    return _cli_collect(workspace)


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
    forms = _collect_entities(workspace, "forms.yaml", "forms")
    for f in forms:
        f.setdefault("name", f["id"])
    agents = _collect_entities(workspace, "agents.yaml", "agents")
    for a in agents:
        a.setdefault("name", a["id"])
    # Apps and config schemas reuse the CLI collectors so the connected-mode
    # bundle stays identical to a disconnected `bifrost deploy` — any entity
    # type missing from the bundle gets DELETED by deploy's reconcile sweep
    # (apps: criteria 12/13; config schemas were wiped on every sync until
    # this collected them).
    from bifrost.commands.solution import (
        _collect_apps,
        _collect_claims,
        _collect_config_schemas,
    )
    from bifrost.solution_descriptor import is_solution_workspace, load_descriptor

    apps = _collect_apps(workspace)
    # The descriptor carries the bundle's declared version (Task 20) — read it
    # the same way zip_install does, so a connected sync records versions too.
    version: str | None = None
    if is_solution_workspace(workspace):
        version = load_descriptor(workspace).version
    return SolutionBundle(
        solution=solution,
        python_files=_collect_python_files(workspace),
        workflows=workflows,
        tables=tables,
        apps=apps,
        forms=forms,
        agents=agents,
        claims=_collect_claims(workspace),
        config_schemas=_collect_config_schemas(workspace),
        version=version,
        # README.md at the repo root → Solution.readme (deploy-owned full-replace;
        # absent => cleared). Read straight off the checkout dir, same mechanism as
        # the manifest collectors above.
        readme=_read_readme(workspace),
    )


def _read_readme(workspace: Path) -> str | None:
    """Read the repo-root ``README.md`` as UTF-8 markdown, or None if absent."""
    path = workspace / "README.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


async def deploy_from_workspace(
    db: AsyncSession, solution: Solution, workspace: Path
) -> DeployResult:
    """Deploy a connected install from an already-checked-out workspace dir.

    This is the testable core of auto-pull (no git): read the workspace, run the
    full-replace deploy's DB phase. ``sync`` wraps this with the clone and runs
    the returned ``finalize_s3`` after committing. The bundle (Python source, app
    inputs) is read fully into memory here, so ``finalize_s3`` is safe to run
    after the checkout dir is gone.

    REFUSES if the checkout is not a Solution workspace (no bifrost.solution.yaml)
    — otherwise an empty/ wrong checkout would full-replace the install down to
    nothing. The descriptor is the workspace marker (§3.8).
    """
    if not (workspace / _DESCRIPTOR_FILENAME).is_file():
        raise NotASolutionWorkspace(
            f"checkout at {workspace} has no {_DESCRIPTOR_FILENAME}; "
            f"refusing to full-replace install {solution.id} from a non-Solution repo"
        )
    bundle = read_workspace_bundle(solution, workspace)
    # force=True: for a connected install the repo's main IS the single writer
    # (one-writer invariant) and auto-pull has no operator in the loop to pass
    # --force — a revert on main (older descriptor version) must still apply,
    # not strand the install behind the downgrade gate. Versions are recorded
    # either way.
    return await SolutionDeployer(db).deploy(bundle, force=True)


async def sync(db: AsyncSession, solution: Solution) -> None:
    """Clone the connected install's repo main and deploy the workspace.

    Called by the auto-pull trigger (webhook/poll) on a new commit to main.

    Serialized per-install with a Redis lock so overlapping triggers can't race —
    an older clone finishing last would otherwise full-replace the newer commit's
    deploy back to a stale state. If the lock is held, this sync is skipped (the
    in-flight one will pick up main's latest, and a follow-up trigger can re-run).
    """
    if not solution.git_connected or not solution.git_repo_url:
        raise ValueError("sync() requires a git-connected solution with a repo url")

    from src.core.redis_client import get_redis_client
    from src.services.solutions.write_lock import (
        SolutionWriteLockHeld,
        solution_write_lock,
    )

    redis = await get_redis_client()._get_redis()
    pending_key = f"bifrost:solution:sync-pending:{solution.id}"

    # Per-install write lock, SHARED with manual deploy (same key namespace) so a
    # sync and a manual deploy can't race either. The lock's TTL is RENEWED while
    # held, so a long clone + npm install + vite build + finalize never loses it
    # mid-deploy (Codex #12).
    #
    # If the lock is held, the in-flight sync ALREADY CLONED its commit, so it
    # won't pick up a newer commit that triggered THIS call. Rather than drop that
    # newer commit (which would leave the install stale until a future trigger —
    # Codex #13), set a "pending rerun" flag; the in-flight holder re-checks it
    # after finalizing and re-syncs, so main's latest is always deployed.
    while True:
        try:
            async with solution_write_lock(solution.id):
                # We hold the lock now: clear any pending marker — we're about to
                # clone the CURRENT main, which subsumes earlier skipped triggers.
                await redis.delete(pending_key)
                await _run_sync_once(db, solution)
        except SolutionWriteLockHeld:
            # Another writer holds it; record that a rerun is owed so the holder
            # picks up this (newer) commit after it finishes.
            await redis.set(pending_key, "1", ex=3600)
            logger.info(
                "Sync already in progress for solution %s; queued a rerun", solution.id
            )
            return

        # Released the lock. If a trigger arrived while we held it, run again so
        # the newest commit lands (bounded: each pass clears the flag under lock,
        # so it only loops while NEW triggers keep arriving).
        if await redis.delete(pending_key):
            logger.info("Rerunning sync for solution %s (newer commit queued)", solution.id)
            continue
        return


async def clone_repo_to_dir(repo_url: str, dest: Path, ref: str | None = None) -> None:
    """Shallow-clone ``repo_url`` (optionally at ``ref``) into ``dest``, off the
    event loop. ``ref`` None => the remote's default branch (GitPython resolves
    HEAD); a branch or tag name otherwise."""
    from git import Repo as GitRepo  # GitPython (already a dep)

    kwargs: dict[str, object] = {"depth": 1}
    if ref:
        kwargs["branch"] = ref  # GitPython --branch accepts a tag or branch
    await asyncio.to_thread(GitRepo.clone_from, repo_url, str(dest), **kwargs)


async def _run_sync_once(db: AsyncSession, solution: Solution) -> None:
    """One clone + deploy + commit + finalize, under the caller's held lock."""
    repo_url = solution.git_repo_url
    assert repo_url is not None  # sync() validated git_connected + git_repo_url
    with tempfile.TemporaryDirectory(prefix=f"bifrost-solution-{solution.slug}-") as tmp:
        work_dir = Path(tmp)
        # clone_from is synchronous/network-bound; run it OFF the event loop so the
        # write-lock's renewal watchdog (and everything else) keeps running during
        # a slow clone — otherwise a long clone would block the loop, starve the
        # watchdog, and let the lock TTL expire mid-deploy.
        await clone_repo_to_dir(repo_url, work_dir, ref=solution.git_ref)
        deploy_root = work_dir / solution.repo_subpath if solution.repo_subpath else work_dir
        if not (deploy_root / _DESCRIPTOR_FILENAME).is_file():
            raise NotASolutionWorkspace(
                f"No {_DESCRIPTOR_FILENAME} at "
                f"{solution.repo_subpath or '<repo root>'} in {repo_url}"
            )
        logger.info("Cloned connected solution %s from %s", solution.id, solution.git_repo_url)
        result = await deploy_from_workspace(db, solution, deploy_root)
    # Commit the DB phase, THEN run S3 — a failed commit changes no running code
    # (Codex P1-c). Both happen while the per-install lock is held so a racing sync
    # can't interleave. The bundle is in-memory, so finalizing after the checkout
    # temp dir is gone is fine.
    await db.commit()
    try:
        await result.finalize_s3()
    except SolutionFinalizeIncomplete:
        # finalize_s3 already retried; storage is down. Auto-pull runs in a
        # background job with no caller to surface a 502 to, and the deploy is
        # full-replace + idempotent — the next sync trigger re-runs and heals it.
        logger.error(
            "Solution %s synced (DB committed) but storage finalize failed "
            "after retries; the next sync will re-run and heal it.",
            solution.id,
        )
