"""
bifrost.solution.yaml — the Solution workspace descriptor.

The descriptor is the root marker that tells tooling (``bifrost run``, deploy,
export) it is operating against a *Solution* workspace rather than the ad-hoc
``_repo/`` workspace, and carries the Solution-level identity + config needed to
target ``_solutions/{id}/`` and stamp ``solution_id`` (success-criteria §3.8).

It does NOT replace the split ``.bifrost/*.yaml`` manifests — those still hold
per-entity content. The descriptor *indexes* them. A Solution workspace =
``bifrost.solution.yaml`` + ``.bifrost/*.yaml`` + Python source + app ``src/``.

Stateless — no DB or S3 dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

DESCRIPTOR_FILENAME = "bifrost.solution.yaml"


class SolutionDescriptor(BaseModel):
    """Parsed ``bifrost.solution.yaml``.

    The descriptor is pure definition — it does NOT carry an install *scope*
    (org vs global). Install kind is the installer's deploy-time choice, set via
    the unified ``--org``/``--global`` standard on ``deploy``/``install``; the
    server derives it from ``organization_id`` (NULL == global). Legacy
    descriptors that still carry a ``scope:`` key load fine — it is ignored
    (``extra="ignore"``).

    ``global_repo_access`` is unrelated to install scope: it controls whether
    the Solution's code may import shared modules from ``_repo/`` (§3.3/§3.5).
    """

    # Ignore unknown/legacy keys (e.g. a pre-standard ``scope:``) so old
    # descriptors keep loading after scope was removed from the schema.
    model_config = ConfigDict(extra="ignore")

    slug: str
    name: str
    # Declared bundle version, recorded on the install at deploy time. Optional
    # and free-form; PEP 440 ordering is only attempted by the server's
    # downgrade gate (unordered versions never block).
    version: str | None = None
    global_repo_access: bool = False
    git_connected: bool = False
    git_repo_url: str | None = None
    # Subfolder of the connected repo holding this descriptor (omni-repo).
    # None => repo root. Set on the install at create/deploy/connect time.
    repo_subpath: str | None = None
    # Git ref (branch/tag) the install tracks. None => default branch.
    git_ref: str | None = None
    # Path to a solution icon image (png/jpeg/svg) relative to the workspace
    # root, e.g. "assets/icon.svg". Shown on the /solutions catalog cards.
    logo: str | None = None


def is_solution_workspace(path: Path | str) -> bool:
    """True if directory ``path`` contains a ``bifrost.solution.yaml``.

    ``path`` is always a workspace directory. The descriptor is resolved as a
    child of it and confined with a startswith barrier, so the only filesystem
    access is on a path proven to live inside the given root — never on the
    caller-supplied string directly.
    """
    root = os.path.realpath(path)
    target = os.path.realpath(os.path.join(root, DESCRIPTOR_FILENAME))
    return target.startswith(root + os.sep) and os.path.isfile(target)


def find_solution_root(start: Path | str) -> Path | None:
    """Walk up from ``start`` (a file or dir) to the nearest Solution root.

    Returns the directory containing ``bifrost.solution.yaml``, or ``None`` if
    none is found before the filesystem root. This is what ``bifrost run`` uses
    to make solution-local imports (``from modules.x import y``) resolve against
    the solution root even when invoked from a subdirectory (criterion 15).
    """
    p = Path(os.path.realpath(start))
    if os.path.isfile(p):
        p = p.parent
    for candidate in (p, *p.parents):
        marker = os.path.realpath(os.path.join(os.path.realpath(candidate), DESCRIPTOR_FILENAME))
        if marker.startswith(os.path.realpath(candidate) + os.sep) and os.path.isfile(marker):
            return candidate
    return None


def load_descriptor(path: Path | str) -> SolutionDescriptor:
    """Load + validate the ``bifrost.solution.yaml`` inside directory ``path``.

    Raises FileNotFoundError if absent, and pydantic ValidationError on a bad
    schema (missing slug/name, etc.). A legacy ``scope:`` key is ignored.

    ``path`` is the workspace directory; the descriptor is always resolved as a
    child of it and confined with a startswith barrier (os.path.realpath
    collapses any ``..``/symlink first), so the read only ever touches a file
    proven to live inside the given root — never the caller string directly.
    """
    root = os.path.realpath(path)
    resolved = os.path.realpath(os.path.join(root, DESCRIPTOR_FILENAME))
    if not resolved.startswith(root + os.sep) or not os.path.isfile(resolved):
        raise FileNotFoundError(f"No {DESCRIPTOR_FILENAME} at {Path(root) / DESCRIPTOR_FILENAME}")
    data = yaml.safe_load(Path(resolved).read_text()) or {}
    return SolutionDescriptor.model_validate(data)
