"""Tripwire: ``configs`` rows are written only through ``config_values``.

``src/services/config_values.py`` is the one place that decides a config
row's stored value and type (encryption, conversion on a type change, an
integration row's schema type). Writers that encoded values themselves are
how secret values ended up stored as plain text. This test fails when code
outside that module:

- constructs the ``Config`` ORM model, or passes it to ``insert``/``update``;
- assigns ``.config_type`` on anything;
- assigns ``.value`` in a module that imports the ``Config`` model.

Use ``new_config_row`` / ``write_config_value`` instead. Git-sync applies
``Upsert`` ops, so its values come from ``stored_manifest_value``; that call
site is the one allowed ``model=Config``.
"""

from __future__ import annotations

import ast
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
OWNER = API_ROOT / "src" / "services" / "config_values.py"
SCANNED = [API_ROOT / "src", API_ROOT / "shared"]
CONFIG_NAMES = {"Config", "ConfigModel"}

# file relative to api/ -> reason
ALLOWED_UPSERT_MODEL = {
    "src/services/manifest_import.py": "git-sync Upsert values come from config_values.stored_manifest_value",
}
# (file relative to api/, assignment target) -> reason
ALLOWED_VALUE_ASSIGNMENTS = {
    ("src/services/solutions/workspace_bundle_import.py", "declaration.value"): (
        "a manifest declaration, not a Config row; git-sync stores it through "
        "config_values.stored_manifest_value"
    ),
}


def _imports_config_model(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "src.models",
            "src.models.orm",
            "src.models.orm.config",
        }:
            if any(alias.name == "Config" for alias in node.names):
                return True
    return False


def _violations(source: str, relative: str) -> list[str]:
    tree = ast.parse(source, filename=relative)
    imports_config = _imports_config_model(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in CONFIG_NAMES and imports_config:
                found.append(f"{relative}:{node.lineno} constructs Config")
            if node.func.id in {"insert", "update"} and any(
                isinstance(arg, ast.Name) and arg.id in CONFIG_NAMES for arg in node.args
            ):
                found.append(f"{relative}:{node.lineno} {node.func.id}(Config)")
            if node.func.id == "Upsert" and relative not in ALLOWED_UPSERT_MODEL:
                for keyword in node.keywords:
                    if (
                        keyword.arg == "model"
                        and isinstance(keyword.value, ast.Name)
                        and keyword.value.id in CONFIG_NAMES
                    ):
                        found.append(f"{relative}:{node.lineno} Upsert(model=Config)")
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Attribute):
                    continue
                if target.attr == "config_type":
                    found.append(f"{relative}:{node.lineno} assigns .config_type")
                if (
                    target.attr == "value"
                    and imports_config
                    and (relative, ast.unparse(target)) not in ALLOWED_VALUE_ASSIGNMENTS
                ):
                    found.append(f"{relative}:{node.lineno} assigns .value")
    return found


def test_config_rows_are_written_only_through_config_values() -> None:
    violations: list[str] = []
    for root in SCANNED:
        for path in sorted(root.rglob("*.py")):
            if path != OWNER:
                violations.extend(
                    _violations(path.read_text(), str(path.relative_to(API_ROOT)))
                )
    assert not violations, (
        "Write configs rows through src.services.config_values "
        "(new_config_row / write_config_value):\n" + "\n".join(violations)
    )


def test_the_tripwire_sees_a_direct_write() -> None:
    sample = (
        "from src.models.orm.config import Config\n"
        "row = Config(key='k', value={'value': 'plain'})\n"
        "row.config_type = 'secret'\n"
        "row.value = {'value': 'plain'}\n"
    )
    assert len(_violations(sample, "src/sample.py")) == 3
