"""
Git-connected Solution auto-pull (success-criteria §3.9, criterion 13).

A git-connected install has exactly one writer: auto-pull from its repo. The
platform clones the connected repo at its configured ref (or the repo's default
branch when none is set) and deploys the workspace found there via
:class:`SolutionDeployer`. ``bifrost deploy`` and the REST deploy
endpoint are refused for a connected install (enforced in the deploy router), so
the one-writer invariant holds.

This module deliberately does NOT touch ``_repo/``: a connected Solution is its
own world, cloned to a throwaway checkout and deployed straight to
``_solutions/{id}/``. It reuses the CLI's own collectors (layout-agnostic Python
source from any folder, ``.bifrost/*.yaml`` manifest) so a git-connected deploy
bundles exactly what ``bifrost deploy`` would.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionBundle, SolutionDeployConflict

logger = logging.getLogger(__name__)

# Root descriptor that marks a Solution workspace (must match
# bifrost.solution_descriptor.DESCRIPTOR_FILENAME).
_DESCRIPTOR_FILENAME = "bifrost.solution.yaml"


class NotASolutionWorkspace(Exception):
    """The checkout has no bifrost.solution.yaml — refuse the full-replace sync."""


@dataclass(frozen=True)
class GitSyncSnapshot:
    """The Git connection that supplied the prepared portable archive."""

    solution_id: UUID
    slug: str
    organization_id: UUID | None
    repo_url: str
    source_id: UUID | None
    ref: str | None
    repo_subpath: str | None

    @classmethod
    def from_solution(cls, solution: Solution) -> "GitSyncSnapshot":
        if not solution.git_connected or not solution.git_repo_url:
            raise ValueError("sync() requires a git-connected solution with a repo url")
        return cls(
            solution_id=solution.id,
            slug=solution.slug,
            organization_id=solution.organization_id,
            repo_url=solution.git_repo_url,
            source_id=solution.git_source_id,
            ref=solution.git_ref,
            repo_subpath=solution.repo_subpath,
        )


def assert_current_git_settings(solution: Solution, snapshot: GitSyncSnapshot) -> None:
    """Refuse publication when the prepared archive's Git connection changed."""
    if (
        solution.id != snapshot.solution_id
        or not solution.git_connected
        or solution.organization_id != snapshot.organization_id
        or solution.git_repo_url != snapshot.repo_url
        or solution.git_source_id != snapshot.source_id
        or solution.git_ref != snapshot.ref
        or solution.repo_subpath != snapshot.repo_subpath
    ):
        raise SolutionDeployConflict(
            "Git connection changed while the Solution update was being prepared."
        )


def resolve_repo_subpath(checkout_root: Path, repo_subpath: str | None) -> Path:
    """Resolve ``repo_subpath`` under ``checkout_root``, guarding against
    traversal (``..``/absolute) escapes. Returns the checkout root when
    ``repo_subpath`` is empty. Raises NotASolutionWorkspace if it escapes.

    ``repo_subpath`` is request-supplied (it comes straight off the install/
    preview-repo HTTP body) and the returned path becomes the root every
    workspace collector reads from, so this is the single traversal chokepoint
    for the whole repo-install flow. Implemented with os.path.realpath + a
    startswith prefix check (the barrier static analysis recognizes for path
    traversal); the trailing os.sep prevents sibling-prefix bypass.
    """
    if not repo_subpath:
        return checkout_root
    base_real = os.path.realpath(checkout_root)
    root = os.path.realpath(os.path.join(base_real, repo_subpath))
    # Pure startswith (not `root != base and not startswith`): a non-empty
    # subpath always resolves to a strict descendant when valid, and the
    # bare-startswith form is the barrier static analysis recognizes.
    if not root.startswith(base_real + os.sep):
        raise NotASolutionWorkspace(
            f"repo_subpath {repo_subpath!r} escapes the repo checkout"
        )
    return Path(root)


def _collect_python_files(workspace: Path, *, definition=None) -> dict[str, str]:
    # Reuse the CLI's canonical, layout-agnostic collector so the git-connected
    # deploy path bundles the SAME files as `bifrost deploy` — a divergent
    # allow-list here is exactly what dropped functions/*.py from CLI deploys
    # (shakeout HIGH). git_sync already imports _collect_apps from the same module.
    from bifrost.commands.solution import _collect_python_files as _cli_collect

    return _cli_collect(workspace, definition=definition)


def read_workspace_bundle(solution: Solution, workspace: Path) -> SolutionBundle:
    """Build a SolutionBundle from a checked-out Solution workspace dir."""
    from bifrost.solution_definition import read_solution_definition

    definition = read_solution_definition(workspace)
    workflows = definition.entries("workflows")
    tables = definition.entries("tables")
    forms = definition.entries("forms")
    agents = definition.entries("agents")
    # Apps and config schemas reuse the CLI collectors so the connected-mode
    # bundle stays identical to a disconnected `bifrost deploy` — any entity
    # type missing from the bundle gets DELETED by deploy's reconcile sweep
    # (apps: criteria 12/13; config schemas were wiped on every sync until
    # this collected them).
    from bifrost.commands.solution import (
        _collect_apps,
    )
    apps = _collect_apps(workspace, definition=definition)
    # The descriptor carries the bundle's declared version (Task 20) — read it
    # the same way zip_install does, so a connected sync records versions too.
    version = definition.descriptor.version if definition.descriptor is not None else None
    return SolutionBundle(
        solution=solution,
        python_files=_collect_python_files(workspace, definition=definition),
        workflows=workflows,
        tables=tables,
        apps=apps,
        forms=forms,
        agents=agents,
        claims=definition.entries("claims"),
        config_schemas=definition.config_entries(),
        file_locations=definition.manifest.files.locations,
        file_policies=definition.install_entries("file_policies"),
        # Declared integrations (connection refs): a git-connected deploy must
        # create the same integration shells + SolutionConnectionSchema rows the
        # zip path does, else a git-installed solution silently drops its
        # integrations and Setup never surfaces them (drive F1).
        connection_schemas=definition.connection_entries(),
        events=definition.entries("events"),
        version=version,
        # README.md at the repo root → Solution.readme (deploy-owned full-replace;
        # absent => cleared). Read straight off the checkout dir, same mechanism as
        # the manifest collectors above.
        readme=_read_readme(workspace),
    )


def _read_readme(workspace: Path) -> str | None:
    """Read the repo-root ``README.md`` as UTF-8 markdown, or None if absent."""
    root = os.path.realpath(workspace)
    target = os.path.realpath(os.path.join(root, "README.md"))
    if not target.startswith(root + os.sep):
        return None
    path = Path(target)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


async def sync(context, solution_id: UUID) -> bool:
    """Clone the connected install's repo at its configured ref and deploy.

    Called by the auto-pull trigger (webhook/poll) on a new commit. The clone
    uses the install's ``git_ref`` when set, or the repo's default branch when
    none is configured. Returns whether this call performed the requested
    update; an existing writer leaves a pending-rerun marker and returns false.

    Serialized per-install with a Redis lock so overlapping triggers can't race —
    an older clone finishing last would otherwise full-replace the newer commit's
    deploy back to a stale state. If the lock is held, this sync is skipped (the
    in-flight one will pick up the latest, and a follow-up trigger can re-run).
    """
    from src.core.database import get_db_context
    from src.core.redis_client import get_redis_client
    from src.jobs.platform.base import PlatformJobCancelled
    from src.services.solutions.write_lock import (
        SolutionWriteLockHeld,
        solution_write_lock,
    )

    async with get_db_context() as db:
        if not await context.authorize_effect(db):
            raise PlatformJobCancelled
        solution = await db.get(Solution, solution_id)
        if solution is None:
            raise SolutionDeployConflict("Solution not found")
        snapshot = GitSyncSnapshot.from_solution(solution)

    redis = await get_redis_client()._get_redis()
    pending_key = f"bifrost:solution:sync-pending:{solution_id}"

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
            async with solution_write_lock(solution_id):
                # We hold the lock now: clear any pending marker — we're about to
                # clone the CURRENT main, which subsumes earlier skipped triggers.
                await redis.delete(pending_key)
                await _run_sync_once(context, snapshot)
        except SolutionWriteLockHeld:
            # Another writer holds it; record that a rerun is owed so the holder
            # picks up this (newer) commit after it finishes.
            await redis.set(pending_key, "1", ex=3600)
            logger.info(
                "Sync already in progress for solution %s; queued a rerun", solution_id
            )
            return False

        # Released the lock. If a trigger arrived while we held it, run again so
        # the newest commit lands (bounded: each pass clears the flag under lock,
        # so it only loops while NEW triggers keep arriving).
        if await redis.delete(pending_key):
            logger.info("Rerunning sync for solution %s (newer commit queued)", solution_id)
            async with get_db_context() as db:
                if not await context.authorize_effect(db):
                    raise PlatformJobCancelled
                solution = await db.get(Solution, solution_id)
                if solution is None:
                    raise SolutionDeployConflict("Solution not found")
                snapshot = GitSyncSnapshot.from_solution(solution)
            continue
        return True


async def clone_repo_to_dir(repo_url: str, dest: Path, ref: str | None = None) -> None:
    """Shallow-clone ``repo_url`` (optionally at ``ref``) into ``dest``, off the
    event loop. ``ref`` None => the remote's default branch (GitPython resolves
    HEAD); a branch or tag name otherwise."""
    from git import Repo as GitRepo  # GitPython (already a dep)

    # Explicit args (not a **kwargs splat) so the call stays fully typed.
    # ``branch`` accepts a tag or a branch name (GitPython --branch).
    if ref is not None:
        await asyncio.to_thread(GitRepo.clone_from, repo_url, str(dest), depth=1, branch=ref)
    else:
        await asyncio.to_thread(GitRepo.clone_from, repo_url, str(dest), depth=1)


async def _run_sync_once(
    context,
    snapshot: GitSyncSnapshot,
) -> None:
    """Prepare a portable Git archive, then publish it through the shared path."""
    from src.core.database import get_db_context
    from src.jobs.platform.base import PlatformJobCancelled
    from src.services.solutions.compute_pipeline import prepare_solution_deployment
    from src.services.solutions.zip_install import _deploy_workspace_to_solution

    with tempfile.TemporaryDirectory(prefix=f"bifrost-solution-{snapshot.slug}-") as tmp:
        work_dir = Path(tmp)
        # clone_from is synchronous/network-bound; run it OFF the event loop so the
        # write-lock's renewal watchdog (and everything else) keeps running during
        # a slow clone — otherwise a long clone would block the loop, starve the
        # watchdog, and let the lock TTL expire mid-deploy.
        from shared.solution_git_sources import clone_solution_repository
        async with get_db_context() as db:
            if not await context.authorize_effect(db):
                raise PlatformJobCancelled
            solution = await db.get(Solution, snapshot.solution_id)
            if solution is None:
                raise SolutionDeployConflict("Solution not found")
            assert_current_git_settings(solution, snapshot)
            # authorize_effect holds the owner/job fence row lock. Credentials
            # may be read from this session for the clone, but network I/O must
            # not retain the fence transaction; publication re-authorizes later.
            await db.commit()
            await clone_solution_repository(
                db,
                snapshot.source_id,
                snapshot.organization_id,
                snapshot.repo_url,
                work_dir,
                snapshot.ref,
            )
        logger.info("Cloned connected solution %s from %s", snapshot.solution_id, snapshot.repo_url)
        deploy_root = resolve_repo_subpath(work_dir, snapshot.repo_subpath)
        if not (deploy_root / _DESCRIPTOR_FILENAME).is_file():
            raise NotASolutionWorkspace(
                f"No {_DESCRIPTOR_FILENAME} at "
                f"{snapshot.repo_subpath or '<repo root>'} in {snapshot.repo_url}"
            )
        from bifrost.commands.solution import _build_deploy_zip

        archive = await asyncio.to_thread(_build_deploy_zip, deploy_root, extra_text_files={})
        zip_path = work_dir / "solution.zip"
        await asyncio.to_thread(zip_path.write_bytes, archive)

        async with prepare_solution_deployment(
            context,
            zip_path,
            solution_id=snapshot.solution_id,
            allow_connected_install=True,
            lock_already_held=True, force=True,
        ) as prepared:
            async with get_db_context() as db:
                if not await context.authorize_effect(db):
                    raise PlatformJobCancelled
                solution = await db.get(Solution, snapshot.solution_id)
                if solution is None:
                    raise SolutionDeployConflict("Solution not found")
                assert_current_git_settings(solution, snapshot)
                result = await _deploy_workspace_to_solution(
                    db,
                    solution,
                    prepared.archive.workspace,
                    force=True,
                    prepared_preview=prepared.archive.preview,
                    prepared_bundle=prepared.bundle,
                    prepared_app_builds=prepared.app_builds,
                    source_artifact=prepared.source_artifact,
                    context=context,
                )
                await db.commit()
                if not await context.authorize_effect(db):
                    raise PlatformJobCancelled
                await result.finalize_s3()
                await db.commit()
