"""What a person holds is the union of their roles' stored permissions.

Platform Admin is recognised by holding the wildcard, never by its role id,
so the code that resolves permissions must not compare against the
Platform Admin role id (or any builtin role id).
"""

from __future__ import annotations

import ast
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[3]

_ROLE_IDENTITY_NAMES = {
    "PLATFORM_ADMIN_ROLE_ID",
    "_PLATFORM_ADMIN_ROLE_ID",
    "USER_ROLE_ID",
    "PLATFORM_OPERATOR_ROLE_ID",
    "DECRYPTION_ROLE_ID",
}


def _role_identity_references(source: str, function: str | None = None) -> list[str]:
    tree = ast.parse(source)
    if function is not None:
        tree = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
        )
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _ROLE_IDENTITY_NAMES:
            found.append(f"{node.id} (line {node.lineno})")
        elif isinstance(node, ast.Attribute) and node.attr in _ROLE_IDENTITY_NAMES:
            found.append(f"{node.attr} (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            found.extend(
                f"{alias.name} imported (line {node.lineno})"
                for alias in node.names
                if alias.name in _ROLE_IDENTITY_NAMES
            )
    return found


def test_authorization_context_does_not_compare_role_ids() -> None:
    source = (API_ROOT / "src/services/authorization/context.py").read_text()
    assert _role_identity_references(source) == []


def test_held_permissions_by_user_does_not_compare_role_ids() -> None:
    source = (API_ROOT / "src/services/authorization/enforce.py").read_text()
    assert _role_identity_references(source, "held_permissions_by_user") == []


def test_role_permissions_service_does_not_compare_role_ids() -> None:
    source = (API_ROOT / "src/services/role_permissions.py").read_text()
    assert _role_identity_references(source) == []
