"""Tripwire: the CLI package metadata must include every bifrost subpackage.

An explicit ``packages = ["bifrost"]`` list ships a wheel with NO subpackages
(bifrost.commands, bifrost.contracts, bifrost.solution_dev, bifrost.tui) on a
clean tree — the CLI import-errors on first use. Historical installs only
worked because stale ``*.egg-info/SOURCES.txt`` artifacts on dev machines
rescued the file list. This test pins the find-directive config and asserts
every on-disk module is discoverable, so a revert or a misconfigured new
subpackage fails CI instead of shipping a broken CLI.
"""

import tarfile
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pydantic

from shared.cli_artifact import build_cli_artifact

API_DIR = Path(__file__).resolve().parents[2]
PYPROJECT = API_DIR / "pyproject.toml"

_SKIP_PARTS = {"__pycache__", "node_modules"}


def test_pyproject_uses_find_directive_not_explicit_list():
    config = tomllib.loads(PYPROJECT.read_text())
    setuptools_cfg = config.get("tool", {}).get("setuptools", {})
    packages_cfg = setuptools_cfg.get("packages")
    assert isinstance(packages_cfg, dict), (
        "api/pyproject.toml must use the [tool.setuptools.packages.find] "
        "directive — an explicit packages list ships a wheel with no "
        "subpackages on a clean tree (broken CLI for source installs)."
    )
    assert "bifrost*" in packages_cfg.get("find", {}).get("include", []), (
        "[tool.setuptools.packages.find] must include 'bifrost*'"
    )


def test_every_module_dir_is_a_discoverable_package():
    """find(include=['bifrost*']) only ships dirs with an unbroken
    __init__.py chain — a .py-bearing dir outside such a chain silently
    drops out of the wheel."""
    root = API_DIR / "bifrost"
    undiscoverable = []
    for py_file in root.rglob("*.py"):
        rel_parts = py_file.parent.relative_to(API_DIR).parts
        if _SKIP_PARTS & set(rel_parts) or any(
            p.endswith(".egg-info") for p in rel_parts
        ):
            continue
        # Every ancestor from bifrost/ down to the module's dir needs __init__.py
        for depth in range(1, len(rel_parts) + 1):
            ancestor = API_DIR.joinpath(*rel_parts[:depth])
            if not (ancestor / "__init__.py").exists():
                undiscoverable.append(str(py_file.relative_to(API_DIR)))
                break
    assert not undiscoverable, (
        f"modules outside an __init__.py package chain (would be omitted "
        f"from the wheel): {sorted(set(undiscoverable))[:10]}"
    )


def test_isolated_cli_archive_parses_solution_file_locations():
    """The distributed CLI must not depend on the server-only ``shared`` package."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        sdk_root = root / "sdk"
        artifact = build_cli_artifact(API_DIR / "bifrost", root / "dist", "1.4.2")
        sdk_root.mkdir()
        with tarfile.open(artifact) as archive:
            archive.extractall(sdk_root, filter="data")
        manifest_dir = root / ".bifrost"
        manifest_dir.mkdir()
        (manifest_dir / "files.yaml").write_text("locations:\n  - migration-data\n")
        code = """
import pathlib
import sys
sys.path[:0] = [sys.argv[1], sys.argv[3]]
from bifrost.manifest import read_manifest_from_dir
root = pathlib.Path(sys.argv[2])
assert read_manifest_from_dir(root).files.locations == ["migration-data"]
for invalid in ("_repo", "workspace"):
    (root / "files.yaml").write_text(f"locations:\\n  - {invalid}\\n")
    try:
        read_manifest_from_dir(root)
    except ValueError:
        pass
    else:
        raise AssertionError(f"accepted invalid location: {invalid}")
"""
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                code,
                str(sdk_root),
                str(manifest_dir),
                str(Path(pydantic.__file__).resolve().parents[1]),
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
