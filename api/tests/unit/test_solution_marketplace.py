import inspect
from pathlib import Path
from unittest.mock import patch

from bifrost.commands.solution import _resolve_target_install
from bifrost.solution_descriptor import SolutionDescriptor
from src.models.orm.solutions import Solution
from src.services.solutions import git_sync
from src.services.solutions.update_check import compute_update_available


def test_solution_has_marketplace_columns():
    cols = set(Solution.__table__.columns.keys())
    assert {"repo_subpath", "git_ref", "update_available_version"} <= cols


def test_descriptor_carries_repo_subpath_and_ref():
    d = SolutionDescriptor(
        slug="s", name="S", repo_subpath="microsoft-csp", git_ref="v1.2.0"
    )
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


def test_resolve_targets_explicit_org():
    installs = [
        {"id": "a", "slug": "s", "scope": "org", "organization_id": "org-A"},
        {"id": "b", "slug": "s", "scope": "org", "organization_id": "org-B"},
    ]
    # Feeding a specific deployer_org_id (as --org would) targets that org's install.
    assert _resolve_target_install(installs, "s", "org", "org-B") == "b"
    assert _resolve_target_install(installs, "s", "org", "org-A") == "a"


def test_deploy_cmd_has_org_option():
    from bifrost.commands.solution import deploy_cmd

    assert "org_ref" in {p.name for p in deploy_cmd.params}


def test_compute_update_available():
    assert compute_update_available(installed="1.0.0", remote="1.1.0") == "1.1.0"
    assert compute_update_available(installed="1.1.0", remote="1.1.0") is None
    assert (
        compute_update_available(installed="1.2.0", remote="1.1.0") is None
    )  # remote older
    assert compute_update_available(installed=None, remote="1.0.0") == "1.0.0"
    assert compute_update_available(installed="1.0.0", remote="not-a-version") is None
    assert compute_update_available(installed="1.0.0", remote=None) is None
    # installed unparseable but remote parseable => signal the remote
    assert compute_update_available(installed="weird", remote="2.0.0") == "2.0.0"


def test_read_dto_exposes_update_available_version():
    from src.models.contracts.solutions import Solution as SolutionDTO

    assert "update_available_version" in SolutionDTO.model_fields
