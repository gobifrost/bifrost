"""Tests for the bifrost.solution.yaml descriptor.

The descriptor is the root marker that tells tooling it is operating against a
Solution workspace (vs the ad-hoc _repo/ workspace) — success-criteria §3.8,
criterion 14. It holds Solution-level identity + config and indexes the existing
split .bifrost/*.yaml manifests (which it does NOT replace).
"""
from __future__ import annotations

import pathlib

import pytest

from bifrost.solution_descriptor import (
    DESCRIPTOR_FILENAME,
    SolutionDescriptor,
    find_solution_root,
    is_solution_workspace,
    load_descriptor,
)


def _write(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    (tmp_path / DESCRIPTOR_FILENAME).write_text(text)
    return tmp_path


def test_load_minimal(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "slug: mna\nname: MNA\nglobal_repo_access: false\n")
    d = load_descriptor(tmp_path)
    assert isinstance(d, SolutionDescriptor)
    assert d.slug == "mna"
    assert d.name == "MNA"
    assert d.global_repo_access is False
    assert is_solution_workspace(tmp_path) is True


def test_defaults(tmp_path: pathlib.Path) -> None:
    """global_repo_access and git_connected default off."""
    _write(tmp_path, "slug: braytel\nname: Braytel\n")
    d = load_descriptor(tmp_path)
    assert d.global_repo_access is False
    assert d.git_connected is False
    assert d.git_repo_url is None


def test_no_scope_field() -> None:
    """The descriptor carries no install scope — install kind is the deploy-time
    --org/--global choice, derived server-side from organization_id."""
    assert "scope" not in SolutionDescriptor.model_fields


def test_legacy_scope_key_is_ignored(tmp_path: pathlib.Path) -> None:
    """A pre-standard descriptor that still carries scope: must keep loading —
    the key is ignored (extra='ignore'), never a validation error."""
    _write(tmp_path, "slug: x\nname: X\nscope: org\n")
    d = load_descriptor(tmp_path)
    assert d.slug == "x"
    assert not hasattr(d, "scope")
    # An otherwise-invalid scope value is ALSO ignored (no longer rejected).
    _write(tmp_path, "slug: y\nname: Y\nscope: tenant\n")
    assert load_descriptor(tmp_path).slug == "y"


def test_git_fields_load(tmp_path: pathlib.Path) -> None:
    _write(
        tmp_path,
        "slug: halo\nname: Halo\n"
        "git_connected: true\ngit_repo_url: https://github.com/x/halo\n",
    )
    d = load_descriptor(tmp_path)
    assert d.git_connected is True
    assert d.git_repo_url == "https://github.com/x/halo"


def test_missing_required_field_rejected(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "name: NoSlug\n")
    with pytest.raises(Exception):
        load_descriptor(tmp_path)


def test_not_a_solution_workspace(tmp_path: pathlib.Path) -> None:
    assert is_solution_workspace(tmp_path) is False
    # A plain _repo/-style workspace (no descriptor) is not a solution.
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "workflows.yaml").write_text("workflows: {}\n")
    assert is_solution_workspace(tmp_path) is False


def test_loads_descriptor_from_workspace_dir(tmp_path: pathlib.Path) -> None:
    # load_descriptor takes the workspace DIRECTORY and resolves the descriptor
    # as a confined child of it (the descriptor read never touches a
    # caller-supplied path directly).
    p = _write(tmp_path, "slug: x\nname: X\n")
    assert load_descriptor(p).slug == "x"


def test_find_solution_root_from_subdir(tmp_path: pathlib.Path) -> None:
    """find_solution_root walks up to the nearest bifrost.solution.yaml."""
    root = _write(tmp_path, "slug: mna\nname: MNA\n")
    nested = root / "workflows" / "sub"
    nested.mkdir(parents=True)
    wf = nested / "w.py"
    wf.write_text("# workflow\n")
    # From a file deep in the tree → the root containing the descriptor.
    assert find_solution_root(wf) == root
    # From a subdirectory → same root.
    assert find_solution_root(nested) == root
    # From the root itself → the root.
    assert find_solution_root(root) == root


def test_find_solution_root_none_when_absent(tmp_path: pathlib.Path) -> None:
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_solution_root(sub) is None


def test_version_round_trips(tmp_path: pathlib.Path) -> None:
    """The descriptor carries an optional bundle version (Task 21)."""
    _write(tmp_path, "slug: mna\nname: MNA\nversion: 1.2.3\n")
    d = load_descriptor(tmp_path)
    assert d.version == "1.2.3"


def test_version_defaults_to_none(tmp_path: pathlib.Path) -> None:
    """Pre-versioning descriptors (no version key) still load."""
    _write(tmp_path, "slug: mna\nname: MNA\n")
    assert load_descriptor(tmp_path).version is None


def test_init_writes_version(tmp_path: pathlib.Path) -> None:
    """`bifrost solution init` writes the version (default 0.1.0) into the
    descriptor, ordered after name, and load_descriptor round-trips it. The
    descriptor carries no scope: key (install kind is a deploy-time choice)."""
    from click.testing import CliRunner

    from bifrost.commands.solution import solution_group

    ws = tmp_path / "ws"
    result = CliRunner().invoke(solution_group, ["init", str(ws), "--slug", "mna"])
    assert result.exit_code == 0, result.output
    assert load_descriptor(ws).version == "0.1.0"
    text = (ws / DESCRIPTOR_FILENAME).read_text()
    assert text.index("name:") < text.index("version:")
    assert "scope:" not in text


def test_init_rejects_scope_flag(tmp_path: pathlib.Path) -> None:
    """`--scope` was removed from init — passing it is a usage error."""
    from click.testing import CliRunner

    from bifrost.commands.solution import solution_group

    ws = tmp_path / "ws"
    result = CliRunner().invoke(
        solution_group, ["init", str(ws), "--slug", "mna", "--scope", "global"]
    )
    assert result.exit_code != 0


def test_init_writes_explicit_version(tmp_path: pathlib.Path) -> None:
    from click.testing import CliRunner

    from bifrost.commands.solution import solution_group

    ws = tmp_path / "ws"
    result = CliRunner().invoke(
        solution_group, ["init", str(ws), "--slug", "mna", "--version", "2.0.0"]
    )
    assert result.exit_code == 0, result.output
    assert load_descriptor(ws).version == "2.0.0"
