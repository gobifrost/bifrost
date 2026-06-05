"""Criterion 15 (offline dev loop): `bifrost run` resolves solution-local
imports against the Solution root even when invoked from a subdirectory.

The workflow executes locally (no network); solution-local modules
(`from modules.x import y`) resolve because the Solution root — detected via
``bifrost.solution.yaml`` above the file — is put on ``sys.path``. The
data-plane half (tables/integrations hitting a live instance) is the SDK's
job and is covered by the table SDK tests; here we prove the import root.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from bifrost.cli import handle_run


@pytest.fixture
def _restore_sys_path():
    before = list(sys.path)
    before_mods = set(sys.modules)
    yield
    sys.path[:] = before
    for name in set(sys.modules) - before_mods:
        sys.modules.pop(name, None)


def test_bifrost_run_resolves_solution_local_import(
    tmp_path: pathlib.Path, capsys, monkeypatch, _restore_sys_path
) -> None:
    # A Solution workspace: descriptor at the root, a local module, and a
    # workflow in workflows/ that imports the local module.
    (tmp_path / "bifrost.solution.yaml").write_text("slug: mna\nname: MNA\nscope: org\n")
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "x.py").write_text("VAL = 42\n")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "w.py").write_text(
        "from bifrost import workflow\n"
        "from modules.x import VAL\n"
        "@workflow\n"
        "async def run():\n"
        "    return VAL\n"
    )

    # Invoke from a DIFFERENT cwd (not the solution root) so resolution can only
    # succeed via the descriptor-detected solution root, not cwd.
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    wf_file = str(tmp_path / "workflows" / "w.py")
    rc = handle_run([wf_file, "-w", "run"])
    assert rc == 0, capsys.readouterr()

    out = capsys.readouterr().out.strip()
    assert out == "42"


def test_bifrost_run_non_solution_file_still_works(
    tmp_path: pathlib.Path, capsys, monkeypatch, _restore_sys_path
) -> None:
    # No descriptor → not a solution; a self-contained workflow still runs
    # (regression: find_solution_root returns None, no sys.path surprise).
    (tmp_path / "w.py").write_text(
        "from bifrost import workflow\n"
        "@workflow\n"
        "async def run():\n"
        "    return 7\n"
    )
    monkeypatch.chdir(tmp_path)
    rc = handle_run([str(tmp_path / "w.py"), "-w", "run"])
    assert rc == 0, capsys.readouterr()
    assert capsys.readouterr().out.strip() == "7"
