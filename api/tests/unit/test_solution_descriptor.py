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
    _write(tmp_path, "slug: mna\nname: MNA\nscope: org\nglobal_repo_access: false\n")
    d = load_descriptor(tmp_path)
    assert isinstance(d, SolutionDescriptor)
    assert d.slug == "mna"
    assert d.name == "MNA"
    assert d.scope == "org"
    assert d.global_repo_access is False
    assert is_solution_workspace(tmp_path) is True


def test_defaults(tmp_path: pathlib.Path) -> None:
    """global_repo_access and git_connected default off; scope defaults to org."""
    _write(tmp_path, "slug: braytel\nname: Braytel\n")
    d = load_descriptor(tmp_path)
    assert d.scope == "org"
    assert d.global_repo_access is False
    assert d.git_connected is False
    assert d.git_repo_url is None


def test_global_scope_and_git(tmp_path: pathlib.Path) -> None:
    _write(
        tmp_path,
        "slug: halo\nname: Halo\nscope: global\n"
        "git_connected: true\ngit_repo_url: https://github.com/x/halo\n",
    )
    d = load_descriptor(tmp_path)
    assert d.scope == "global"
    assert d.git_connected is True
    assert d.git_repo_url == "https://github.com/x/halo"


def test_invalid_scope_rejected(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "slug: x\nname: X\nscope: tenant\n")
    with pytest.raises(Exception):
        load_descriptor(tmp_path)


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


def test_accepts_file_path_or_dir(tmp_path: pathlib.Path) -> None:
    p = _write(tmp_path, "slug: x\nname: X\n")
    by_dir = load_descriptor(p)
    by_file = load_descriptor(p / DESCRIPTOR_FILENAME)
    assert by_dir.slug == by_file.slug == "x"


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
    descriptor, ordered after name and before scope for readability, and
    load_descriptor round-trips it."""
    from click.testing import CliRunner

    from bifrost.commands.solution import solution_group

    ws = tmp_path / "ws"
    result = CliRunner().invoke(solution_group, ["init", str(ws), "--slug", "mna"])
    assert result.exit_code == 0, result.output
    assert load_descriptor(ws).version == "0.1.0"
    text = (ws / DESCRIPTOR_FILENAME).read_text()
    assert text.index("name:") < text.index("version:") < text.index("scope:")


def test_init_writes_explicit_version(tmp_path: pathlib.Path) -> None:
    from click.testing import CliRunner

    from bifrost.commands.solution import solution_group

    ws = tmp_path / "ws"
    result = CliRunner().invoke(
        solution_group, ["init", str(ws), "--slug", "mna", "--version", "2.0.0"]
    )
    assert result.exit_code == 0, result.output
    assert load_descriptor(ws).version == "2.0.0"
