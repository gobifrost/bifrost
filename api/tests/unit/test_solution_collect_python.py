"""`bifrost solution deploy` must bundle ALL of a solution's Python source,
regardless of which folder the developer put it in.

Shakeout HIGH (2026-06-08): _collect_python_files only scanned a fixed allow-list
(workflows/, modules/, shared/), so a workflow under functions/ — exactly where
`solution scaffold-app` writes its sample (functions/hello.py) and where
`solution start` discovers it — deployed with a Workflow ROW but ZERO code. The
collector must be layout-agnostic (like the local function host), excluding only
generated/dep/manifest dirs and the separately-bundled app source dirs.
"""
from __future__ import annotations

import pathlib

import yaml

from bifrost.commands.solution import _collect_python_files


def _write(p: pathlib.Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_collects_python_from_any_folder(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "bifrost.solution.yaml", "slug: s\nname: S\nscope: org\n")
    _write(tmp_path / "functions/hello.py", "x = 1\n")     # the scaffold's location
    _write(tmp_path / "modules/calc.py", "y = 2\n")        # already-allowed
    _write(tmp_path / "shared/util.py", "z = 3\n")         # already-allowed
    _write(tmp_path / "lib/helpers.py", "w = 4\n")         # an arbitrary dir

    files = _collect_python_files(tmp_path)

    assert "functions/hello.py" in files
    assert "modules/calc.py" in files
    assert "shared/util.py" in files
    assert "lib/helpers.py" in files


def test_skips_generated_dep_and_manifest_dirs(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / "bifrost.solution.yaml", "slug: s\nname: S\nscope: org\n")
    _write(tmp_path / "functions/hello.py", "x = 1\n")
    _write(tmp_path / "node_modules/pkg/index.py", "bad = 1\n")
    _write(tmp_path / ".venv/lib/thing.py", "bad = 1\n")
    _write(tmp_path / "__pycache__/cache.py", "bad = 1\n")
    _write(tmp_path / ".bifrost/whatever.py", "bad = 1\n")

    files = _collect_python_files(tmp_path)

    assert "functions/hello.py" in files
    assert not any("node_modules" in k for k in files)
    assert not any(".venv" in k for k in files)
    assert not any("__pycache__" in k for k in files)
    assert not any(".bifrost" in k for k in files)


def test_excludes_app_source_dirs(tmp_path: pathlib.Path) -> None:
    # Apps are bundled separately by _collect_apps; their dir's .py must NOT be
    # double-collected as workflow source (an app could ship a build script).
    _write(tmp_path / "bifrost.solution.yaml", "slug: s\nname: S\nscope: org\n")
    _write(tmp_path / "functions/hello.py", "x = 1\n")
    _write(tmp_path / "apps/dash/scripts/build.py", "app_only = 1\n")
    (tmp_path / ".bifrost").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        yaml.safe_dump({"apps": {"a": {"id": "a", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"}}})
    )

    files = _collect_python_files(tmp_path)

    assert "functions/hello.py" in files
    assert "apps/dash/scripts/build.py" not in files
    assert not any(k.startswith("apps/dash/") for k in files)
