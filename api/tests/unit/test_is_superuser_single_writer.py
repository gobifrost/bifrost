"""Structural lint: `User.is_superuser` has exactly one writer.

`shared.sdk_users.set_user_base_role` is the single writer that keeps
`is_superuser == (base_role_id == PLATFORM_ADMIN_ROLE_ID)`. Every other
direct assignment (`x.is_superuser = ...`, or `is_superuser=...` passed to
a `User(...)`/`UserORM(...)` constructor call) is a latent invariant-
breaking bug waiting to happen — this test AST-walks `api/src` and
`api/shared` for one and fails loudly if it finds one outside the
allow-listed file.

An AST walk (not a text regex) is deliberate: a regex on `is_superuser\\s*=`
would also match every `is_superuser: bool = False` function-parameter
default and every unrelated `is_superuser=...` keyword argument passed to
things like `OrgScopedRepository`/`Principal` construction (there are
dozens of those, all legitimate). The AST only flags an attribute
assignment or a keyword argument on a call literally named `User`/
`UserORM`.
"""

from __future__ import annotations

import ast
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]

# The only file allowed to write `User.is_superuser`. It implements
# `set_user_base_role`, the single writer.
ALLOWED_FILES = {
    "shared/sdk_users.py",
}

# `self.is_superuser = is_superuser` here is an OrgScopedRepository
# *instance* flag (scope-bypass authority for that repository call), not a
# write to a `User` row's `is_superuser` column. Same identifier, unrelated
# concept — allow-listed rather than renamed to keep this diff scoped.
ALLOWED_ATTRIBUTE_ASSIGNMENTS = {
    "src/repositories/org_scoped.py",
}

_USER_CONSTRUCTOR_NAMES = {"User", "UserORM"}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _scan_file(path: Path) -> list[str]:
    offenders: list[str] = []
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "is_superuser":
                    offenders.append(f"{path}:{node.lineno}: attribute assignment to .is_superuser")
        elif isinstance(node, ast.Call) and _call_name(node) in _USER_CONSTRUCTOR_NAMES:
            for kw in node.keywords:
                if kw.arg == "is_superuser":
                    offenders.append(
                        f"{path}:{node.lineno}: is_superuser= passed to a {_call_name(node)}(...) constructor"
                    )
    return offenders


def _scan(root: Path) -> list[str]:
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(API_ROOT))
        if rel in ALLOWED_FILES or rel in ALLOWED_ATTRIBUTE_ASSIGNMENTS:
            continue
        offenders.extend(_scan_file(path))
    return offenders


def test_no_direct_is_superuser_writes_outside_the_single_writer():
    offenders: list[str] = []
    for sub in ("src", "shared"):
        offenders.extend(_scan(API_ROOT / sub))

    assert not offenders, (
        "Direct is_superuser write(s) found outside "
        "shared/sdk_users.py::set_user_base_role. Route them through "
        "set_user_base_role instead so base_role_id stays in lockstep:\n"
        + "\n".join(offenders)
    )


def test_allowed_file_still_defines_the_single_writer():
    """If the allow-listed file stops matching, the allow-list is stale."""
    text = (API_ROOT / "shared" / "sdk_users.py").read_text()
    assert "def set_user_base_role(" in text
    assert "user.is_superuser = role_id == PLATFORM_ADMIN_ROLE_ID" in text
