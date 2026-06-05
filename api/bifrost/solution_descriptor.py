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

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

DESCRIPTOR_FILENAME = "bifrost.solution.yaml"


class SolutionDescriptor(BaseModel):
    """Parsed ``bifrost.solution.yaml``.

    ``scope`` is the install scope (``org`` or ``global``) — visibility of the
    deployed entities. ``global_repo_access`` is orthogonal: whether the
    Solution's code may import shared modules from ``_repo/`` (§3.3/§3.5).
    """

    slug: str
    name: str
    scope: Literal["org", "global"] = "org"
    global_repo_access: bool = False
    git_connected: bool = False
    git_repo_url: str | None = None
    # Declared shared deps to vendor on export (filled in Sub-plan 4). Kept here
    # so the descriptor schema is stable across sub-plans.
    deps: list[str] = Field(default_factory=list)


def _descriptor_path(path: Path | str) -> Path:
    """Resolve ``path`` (a workspace dir OR the descriptor file) to the file."""
    p = Path(path)
    if p.is_dir():
        return p / DESCRIPTOR_FILENAME
    return p


def is_solution_workspace(path: Path | str) -> bool:
    """True if ``path`` (a dir) contains a ``bifrost.solution.yaml``."""
    return _descriptor_path(path).is_file()


def find_solution_root(start: Path | str) -> Path | None:
    """Walk up from ``start`` (a file or dir) to the nearest Solution root.

    Returns the directory containing ``bifrost.solution.yaml``, or ``None`` if
    none is found before the filesystem root. This is what ``bifrost run`` uses
    to make solution-local imports (``from modules.x import y``) resolve against
    the solution root even when invoked from a subdirectory (criterion 15).
    """
    p = Path(start).resolve()
    if p.is_file():
        p = p.parent
    for candidate in (p, *p.parents):
        if (candidate / DESCRIPTOR_FILENAME).is_file():
            return candidate
    return None


def load_descriptor(path: Path | str) -> SolutionDescriptor:
    """Load + validate the descriptor at ``path`` (a workspace dir or the file).

    Raises FileNotFoundError if absent, and pydantic ValidationError on a bad
    schema (unknown scope, missing slug/name, etc.).
    """
    descriptor_file = _descriptor_path(path)
    if not descriptor_file.is_file():
        raise FileNotFoundError(f"No {DESCRIPTOR_FILENAME} at {descriptor_file}")
    data = yaml.safe_load(descriptor_file.read_text()) or {}
    return SolutionDescriptor.model_validate(data)
