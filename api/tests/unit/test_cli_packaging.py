"""Tripwire: the CLI package metadata must include every bifrost subpackage.

An explicit ``packages = ["bifrost"]`` list ships a wheel with NO subpackages
(bifrost.commands, bifrost.contracts, bifrost.solution_dev, bifrost.tui) on a
clean tree — the CLI import-errors on first use. Historical installs only
worked because stale ``*.egg-info/SOURCES.txt`` artifacts on dev machines
rescued the file list. This test pins the find-directive config and asserts
every on-disk module is discoverable, so a revert or a misconfigured new
subpackage fails CI instead of shipping a broken CLI.
"""

import tomllib
from pathlib import Path

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
