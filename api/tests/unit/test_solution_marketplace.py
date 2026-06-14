import inspect
from pathlib import Path
from unittest.mock import patch

from bifrost.solution_descriptor import SolutionDescriptor
from src.models.orm.solutions import Solution
from src.services.solutions import git_sync


def test_solution_has_marketplace_columns():
    cols = set(Solution.__table__.columns.keys())
    assert {"repo_subpath", "git_ref", "update_available_version"} <= cols


def test_descriptor_carries_repo_subpath_and_ref():
    d = SolutionDescriptor(slug="s", name="S", repo_subpath="microsoft-csp", git_ref="v1.2.0")
    assert d.repo_subpath == "microsoft-csp"
    assert d.git_ref == "v1.2.0"
    d2 = SolutionDescriptor(slug="s", name="S")
    assert d2.repo_subpath is None and d2.git_ref is None


def test_clone_helper_signature():
    params = inspect.signature(git_sync.clone_repo_to_dir).parameters
    assert {"repo_url", "dest", "ref"} <= set(params)


async def test_clone_ref_none_omits_branch_kwarg():
    with patch("git.Repo.clone_from") as clone_from:
        await git_sync.clone_repo_to_dir("file:///x", Path("/tmp/x"), ref=None)
    _, kwargs = clone_from.call_args
    assert "branch" not in kwargs
    assert kwargs.get("depth") == 1


async def test_clone_ref_set_passes_branch():
    with patch("git.Repo.clone_from") as clone_from:
        await git_sync.clone_repo_to_dir("file:///x", Path("/tmp/x"), ref="v1.2.0")
    _, kwargs = clone_from.call_args
    assert kwargs.get("branch") == "v1.2.0"
