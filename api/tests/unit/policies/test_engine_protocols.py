"""Engine-only tests proving the engine is domain-agnostic.

These tests use a stub Resolver/Binding rather than table-specific ones,
proving the walker code does not reach into any domain.
"""
from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import Column, Integer, String, column
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import ColumnElement

from shared.policies.ast import (
    Expr,
    Policy as SharedPolicy,
    PolicyDocument,
)
from shared.policies.binding import Binding
from shared.policies.compile import compile_to_sql
from shared.policies.evaluate import evaluate as _engine_evaluate
from shared.policies.resolver import Resolver


class StubResolver:
    namespace: ClassVar[str] = "row"

    def resolve(self, path: str, ctx: Any) -> Any:
        return (ctx or {}).get(path)


def test_resolver_protocol_runtime_check():
    """StubResolver structurally satisfies the Resolver protocol."""
    r: Resolver = StubResolver()
    assert r.namespace == "row"
    assert r.resolve("name", {"name": "alice"}) == "alice"
    assert r.resolve("missing", {}) is None
    assert r.resolve("name", None) is None


class StubBinding:
    namespace: ClassVar[str] = "row"

    def resolve_reference(self, path: str) -> ColumnElement[Any]:
        return column(path)


def test_binding_protocol_runtime_check():
    """StubBinding structurally satisfies the Binding protocol."""
    b: Binding = StubBinding()
    assert isinstance(b, Binding)
    assert b.namespace == "row"
    col = b.resolve_reference("name")
    assert str(col) == "name"


def test_policy_document_round_trip():
    """PolicyDocument validates with any action vocab as plain strings (domain-agnostic)."""
    doc = PolicyDocument.model_validate({
        "policies": [
            {
                "name": "admin",
                "actions": ["read", "write", "list", "delete"],  # file action vocab — accepted at shared layer
                "when": {"user": "is_platform_admin"},
            },
        ],
    })
    assert len(doc.policies) == 1
    assert doc.policies[0].name == "admin"
    assert doc.policies[0].actions == ["read", "write", "list", "delete"]


def test_policy_document_empty():
    """Empty PolicyDocument is valid (default-deny semantics)."""
    doc = PolicyDocument()
    assert doc.policies == []


def test_shared_policy_accepts_arbitrary_actions():
    """Shared Policy is domain-agnostic — accepts any action string list."""
    p = SharedPolicy(name="x", actions=["custom_action"])
    assert p.actions == ["custom_action"]


def test_expr_validator_still_works():
    """AST validator still rejects unknown user fields."""
    import pytest
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        Expr.model_validate({"user": "not_a_real_field"})


class _UserForEvalTest:
    user_id = "u-1"
    email = "u@x"
    organization_id = None
    is_platform_admin = False
    role_ids: list = []
    role_names: list = []


def test_evaluate_with_stub_resolver():
    """Engine evaluates `{row: ...}` references via the Resolver — no domain code in walker."""
    expr = Expr.model_validate({"eq": [{"row": "owner_id"}, {"user": "user_id"}]})
    resolver = StubResolver()
    assert _engine_evaluate(expr, ctx={"owner_id": "u-1"}, user=_UserForEvalTest(), resolver=resolver) is True
    assert _engine_evaluate(expr, ctx={"owner_id": "u-2"}, user=_UserForEvalTest(), resolver=resolver) is False


def test_evaluate_with_alternate_namespace():
    """A different-namespace resolver handles its own references."""

    class FileResolverStub:
        namespace = "file"

        def resolve(self, path: str, ctx):
            return (ctx or {}).get(path)

    expr = Expr.model_validate({"eq": [{"file": "created_by"}, {"user": "user_id"}]})
    resolver = FileResolverStub()
    assert _engine_evaluate(expr, ctx={"created_by": "u-1"}, user=_UserForEvalTest(), resolver=resolver) is True


_Base = declarative_base()


class _DocFixture(_Base):
    __tablename__ = "_test_doc_for_compile"
    id = Column(Integer, primary_key=True)
    owner_id = Column(String)


class _StubBindingForCompile:
    namespace = "row"

    def resolve_reference(self, path: str):
        col = getattr(_DocFixture, path, None)
        if col is None:
            raise ValueError(f"unknown column: {path}")
        return col


def test_compile_with_stub_binding():
    """compile_to_sql delegates {row: ...} resolution to the Binding — no Document import in walker."""
    expr = Expr.model_validate({"eq": [{"row": "owner_id"}, {"user": "user_id"}]})
    sql = compile_to_sql(expr, user=_UserForEvalTest(), binding=_StubBindingForCompile())
    rendered = str(sql.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_id" in rendered


_FORBIDDEN_DOMAIN_PREFIXES = (
    "src.models.orm",
    "shared.table_policies",
    "shared.file_policies",
)


def _scan_for_forbidden_imports(source: str, label: str) -> list[str]:
    """Return a list of forbidden imports found in `source`.

    Shared between the real-engine assertion and a negative self-check
    that proves the scanner actually catches what it claims to.
    """
    import ast as _ast

    bad: list[str] = []
    tree = _ast.parse(source)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom) and node.module:
            if any(node.module.startswith(p) for p in _FORBIDDEN_DOMAIN_PREFIXES):
                bad.append(f"{label}: from {node.module}")
        elif isinstance(node, _ast.Import):
            for alias in node.names:
                if any(alias.name.startswith(p) for p in _FORBIDDEN_DOMAIN_PREFIXES):
                    bad.append(f"{label}: import {alias.name}")
    return bad


def test_engine_does_not_import_domain_code():
    """The shared engine modules must not import any table-specific code.

    Static analysis: parse each module under `shared/policies/` and assert
    nothing imports `src.models.orm`, `shared.table_policies`, or
    `shared.file_policies`. This guards against future regressions that
    would re-couple the engine to a specific domain.
    """
    import pathlib

    here = pathlib.Path(__file__).resolve()
    # tests/unit/policies/test_engine_protocols.py -> api/shared/policies/
    engine_root = here.parents[3] / "shared" / "policies"
    assert engine_root.is_dir(), f"engine root not found at {engine_root}"

    bad: list[str] = []
    for py in sorted(engine_root.rglob("*.py")):
        bad.extend(_scan_for_forbidden_imports(py.read_text(), py.name))

    assert not bad, "engine reaches into domain code:\n" + "\n".join(bad)


def test_forbidden_import_scanner_catches_violations():
    """Self-check: prove the scanner actually flags violations.

    Without this, a buggy scanner that always returned [] would make the
    engine isolation test silently pass forever.
    """
    cases = [
        "from src.models.orm.tables import Document",
        "from shared.table_policies import RowResolver",
        "from shared.file_policies import FileResolver",
        "import src.models.orm.tables",
    ]
    for src in cases:
        assert _scan_for_forbidden_imports(src, "synthetic"), (
            f"scanner missed forbidden import: {src!r}"
        )

    # And the inverse — clean imports must NOT be flagged.
    clean = (
        "from shared.policies.ast import Expr\n"
        "from typing import Any\n"
        "import sqlalchemy\n"
    )
    assert _scan_for_forbidden_imports(clean, "synthetic") == []
