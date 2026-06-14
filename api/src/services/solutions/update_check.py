"""Update-available detection for git-connected Solution installs.

The remote version is the ``version:`` field of bifrost.solution.yaml at the
connected ref's HEAD — NOT a git tag. A repo-wide tag cannot version N
solutions in an omni-repo's subfolders, so detection is descriptor-driven and
needs no release ceremony (authors just bump ``version:``)."""
from __future__ import annotations

from pathlib import Path

from packaging.version import InvalidVersion, Version


def compute_update_available(*, installed: str | None, remote: str | None) -> str | None:
    """Return ``remote`` if it is a clean PEP-440 increment over ``installed``,
    else None. Unparseable remote, or remote <= installed, => None (no signal).
    An unparseable *installed* with a parseable remote => signal the remote
    (we can't order them, but a parseable newer descriptor is worth surfacing)."""
    if not remote:
        return None
    try:
        rv = Version(remote)
    except InvalidVersion:
        return None
    if installed is None:
        return remote
    try:
        iv = Version(installed)
    except InvalidVersion:
        return remote
    return remote if rv > iv else None


async def fetch_remote_version(
    *, repo_url: str, repo_subpath: str | None, ref: str | None
) -> str | None:
    """Shallow-clone ``repo_url`` at ``ref`` and read the descriptor ``version:``
    at ``repo_subpath``. Returns None if the repo/descriptor can't be read (the
    caller logs and skips). Never raises for a bad repo — the update-check sweep
    must not abort on one unreachable install."""
    import tempfile

    from bifrost.solution_descriptor import is_solution_workspace, load_descriptor
    from src.services.solutions.git_sync import (
        clone_repo_to_dir,
        resolve_repo_subpath,
    )

    with tempfile.TemporaryDirectory(prefix="bifrost-update-check-") as tmp:
        work = Path(tmp)
        try:
            await clone_repo_to_dir(repo_url, work, ref=ref)
            root = resolve_repo_subpath(work, repo_subpath)
        except Exception:  # noqa: BLE001 - any clone/subpath failure => no signal; the sweep skips this install rather than aborting
            return None
        if not is_solution_workspace(root):
            return None
        return load_descriptor(root).version
