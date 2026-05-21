# Policy Engine Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `api/shared/policies/` into a domain-agnostic engine consumed via `Resolver` + `Binding` protocols, with table-specific code moved into `api/shared/table_policies.py`. Tables behavior must remain bit-for-bit unchanged. This is the precondition for the file-policies plan (#170) and is independently valuable per design doc §10.2.

**Architecture:** Today `api/shared/policies/` reaches into table-specific ORM models (`Document`, `_COLUMN_MAPPED_ROW_FIELDS`, hardcoded `{row}` reference namespace, `TablePolicies` class name, table action vocab). We introduce two Protocol-based seams: a `Resolver` (resolves `{<namespace>: path}` references against a domain context at evaluate time) and a `Binding` (resolves the same references against a SQL backend at compile time). The walker code is otherwise unchanged. Table-specific code (RowResolver, TableBinding, `compile_read_filter`, `make_seed_admin_bypass` with table action vocab) moves to `api/shared/table_policies.py`. After this refactor, file-policies can land by adding `api/shared/file_policies.py` (FileResolver, no Binding — files have no SQL surface) without touching the engine.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, Pydantic v2, pytest. No new runtime deps.

---

## Pre-flight

This plan modifies `api/shared/policies/` and 8 consumer modules. The test suite for the engine (`api/tests/unit/policies/`) is the safety net — every refactor task ends with that suite still green.

The refactor is **mechanical**: protocol indirection + file moves. There is no behavior change. The tasks are structured so that each one ends with a green test suite; do not batch.

### Files to be created

| Path | Responsibility |
|------|---------------|
| `api/shared/policies/ast.py` | `Expr`, `Policy`, `PolicyDocument`, AST validator. Domain-agnostic. Imports `FUNCTIONS` from `shared.policies.functions`. |
| `api/shared/policies/resolver.py` | `Resolver` protocol. Reference resolution at evaluate time. |
| `api/shared/policies/binding.py` | `Binding` protocol. Reference resolution at compile time (for domains with SQL pushdown). |
| `api/shared/table_policies.py` | `RowResolver`, `TableBinding`, `compile_read_filter`, `make_seed_admin_bypass` (table action vocab), `TablePolicies` re-export. |
| `api/tests/unit/policies/test_engine_protocols.py` | Engine-only tests using a stub Resolver/Binding. Proves the engine is domain-agnostic. |

### Files to be modified

| Path | Change |
|------|--------|
| `api/shared/policies/evaluate.py` | Accept `Resolver`; remove `{row}`-specific code. `row: dict \| None` becomes `ctx: Any`. |
| `api/shared/policies/compile.py` | Accept `Binding`; remove `Document`/`_COLUMN_MAPPED_ROW_FIELDS`. |
| `api/shared/policies/probe.py` | Drop `compile_read_filter` (moves to `table_policies.py`); drop `make_seed_admin_bypass` (moves to `table_policies.py`). Keep `evaluate_action`, `is_subscribe_authorized`, `_is_purely_user_dependent`. |
| `api/shared/policies/subscription.py` | Replace `TablePolicies` → `PolicyDocument`. Accept `Resolver`. |
| `api/shared/policies/functions.py` | No code change. Confirm domain-agnostic. |
| `api/src/models/contracts/policies.py` | Becomes a thin re-export shim for backward-compat: `from shared.policies.ast import Expr, Policy, PolicyDocument as TablePolicies`. Keep `PolicyValidationError`/`PolicyValidationResponse` (response models — not engine code). |
| `api/src/routers/tables.py` | Import `compile_read_filter`, `evaluate_action` from `shared.table_policies` (action-OR over rules) and pass `RowResolver()` / `TableBinding()` into engine helpers. |
| `api/src/routers/websocket.py` | Pass `RowResolver()` into `decide_visibility_change` and `is_subscribe_authorized`. |
| `api/src/routers/cli.py`, `api/src/services/manifest_import.py`, `api/src/services/mcp_server/tools/tables.py`, `api/src/routers/export_import.py` | `make_seed_admin_bypass` import moves to `shared.table_policies`. |
| `api/tests/unit/policies/test_evaluate.py` | Adapt to new `Resolver`-based signature. |
| `api/tests/unit/policies/test_compile.py` | Adapt to new `Binding`-based signature; pass `TableBinding()`. |
| `api/tests/unit/policies/test_probe.py` | `make_seed_admin_bypass` import moves; `compile_read_filter` import moves. |
| `api/tests/unit/policies/test_subscription_logic.py` | Pass `RowResolver()`. |
| `api/tests/unit/policies/test_round_trip.py` | Pass `RowResolver()` + `TableBinding()`. |
| `api/tests/unit/test_admin_bypass_seed_migration.py` | `make_seed_admin_bypass` import moves. |

### Public API contract (after refactor)

```python
# api/shared/policies/ast.py
class Expr(RootModel[dict]): ...
class Policy(BaseModel):
    name: str
    description: str | None
    actions: list[str]  # generic; domains re-type via Literal
    when: Expr | None
class PolicyDocument(BaseModel):
    policies: list[Policy]

# api/shared/policies/resolver.py
class Resolver(Protocol):
    namespace: ClassVar[str]  # "row" | "file" | ...
    def resolve(self, path: str, ctx: Any) -> Any: ...

# api/shared/policies/binding.py
class Binding(Protocol):
    namespace: ClassVar[str]
    def resolve_reference(self, path: str) -> ColumnElement[Any]: ...

# api/shared/policies/evaluate.py
def evaluate(expr: Expr, ctx: Any, user: Any, resolver: Resolver) -> bool: ...

# api/shared/policies/compile.py
def compile_to_sql(expr: Expr, user: Any, binding: Binding) -> ColumnElement[Any]: ...

# api/shared/policies/probe.py
def evaluate_action(action: str, policies: PolicyDocument, ctx: Any, user: Any, resolver: Resolver) -> bool: ...
def is_subscribe_authorized(policies: PolicyDocument, user: Any, resolver: Resolver) -> bool: ...

# api/shared/policies/subscription.py
def decide_visibility_change(
    old_ctx: dict | None,
    new_ctx: dict | None,
    policies: PolicyDocument,
    user: Any,
    resolver: Resolver,
    user_filter: Expr | None = None,
) -> tuple[Action, dict | str | None] | None: ...

# api/shared/table_policies.py
class RowResolver:
    namespace = "row"
    def resolve(self, path: str, ctx: dict | None) -> Any: ...
class TableBinding:
    namespace = "row"
    def resolve_reference(self, path: str) -> ColumnElement[Any]: ...
def compile_read_filter(policies: PolicyDocument, user: Any) -> ColumnElement[Any] | None: ...
def make_seed_admin_bypass() -> dict: ...
TablePolicies = PolicyDocument  # re-export
```

---

## Task 1: Boot test stack and confirm baseline

**Files:** None (verification step)

- [ ] **Step 1: Boot test stack**

Run: `./test.sh stack up`
Expected: Stack boots; no errors.

- [ ] **Step 2: Run existing policies tests; confirm baseline green**

Run: `./test.sh tests/unit/policies/ -v`
Expected: All tests pass. If any fail, stop and investigate before refactoring.

- [ ] **Step 3: Capture baseline test count**

Run: `./test.sh tests/unit/policies/ --collect-only -q | tail -5`
Expected: A number like "47 tests collected". Record this number — every refactor task must end with the same count (or more) passing.

---

## Task 2: Create `Resolver` and `Binding` protocols

**Files:**
- Create: `api/shared/policies/resolver.py`
- Create: `api/shared/policies/binding.py`

- [ ] **Step 1: Write the failing test for protocol shape**

Create: `api/tests/unit/policies/test_engine_protocols.py`

```python
"""Engine-only tests proving the engine is domain-agnostic.

These tests use a stub Resolver/Binding rather than table-specific ones,
proving the walker code does not reach into any domain.
"""
from __future__ import annotations

from typing import Any, ClassVar

from shared.policies.binding import Binding
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


def test_binding_protocol_exists():
    """Binding protocol importable and has required attributes."""
    assert hasattr(Binding, "namespace")
    assert hasattr(Binding, "resolve_reference")
```

- [ ] **Step 2: Run test to verify it fails (import error)**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py -v`
Expected: FAIL — `ModuleNotFoundError: shared.policies.resolver` (or `.binding`).

- [ ] **Step 3: Write the protocol modules**

Create: `api/shared/policies/resolver.py`

```python
"""Resolver protocol — domain-agnostic reference resolution at evaluate time.

Each domain (tables, files, ...) implements a Resolver that knows how to look
up `{<namespace>: path}` references against its context shape. The evaluator
walker treats Resolver opaquely; the namespace string is the only piece of
domain knowledge the walker carries.
"""
from __future__ import annotations

from typing import Any, ClassVar, Protocol, runtime_checkable


@runtime_checkable
class Resolver(Protocol):
    """Resolves `{<namespace>: path}` references against a domain context."""

    namespace: ClassVar[str]
    """The reference namespace key this resolver handles. E.g. "row" for tables,
    "file" for files. The walker compares against `set(node.keys()) == {namespace}`.
    """

    def resolve(self, path: str, ctx: Any) -> Any:
        """Resolve a dot-path against the domain ctx. Missing returns None."""
        ...
```

Create: `api/shared/policies/binding.py`

```python
"""Binding protocol — domain-agnostic reference resolution at compile time.

Domains with a SQL surface (tables) implement a Binding that maps
`{<namespace>: path}` references to SQLAlchemy column expressions. Domains
without a SQL surface (files — list operations are S3 prefix-bound; the
evaluator filters in Python) do not implement Binding.
"""
from __future__ import annotations

from typing import Any, ClassVar, Protocol, runtime_checkable

from sqlalchemy.sql import ColumnElement


@runtime_checkable
class Binding(Protocol):
    """Resolves `{<namespace>: path}` references to SQLAlchemy columns."""

    namespace: ClassVar[str]
    """Must match the matching Resolver's namespace for the same domain."""

    def resolve_reference(self, path: str) -> ColumnElement[Any]:
        """Map a dot-path into a SQLAlchemy column expression."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```bash
git add api/shared/policies/resolver.py api/shared/policies/binding.py api/tests/unit/policies/test_engine_protocols.py
git commit -m "feat(policies): introduce Resolver and Binding protocols

Domain-agnostic seams for the policy engine. Resolver handles
reference lookup at evaluate time; Binding handles it at compile time.
Subsequent commits move table-specific code behind these protocols."
```

---

## Task 3: Extract AST types into `shared/policies/ast.py`

**Files:**
- Create: `api/shared/policies/ast.py`
- Modify: `api/src/models/contracts/policies.py` (convert to re-export shim)

The current `api/src/models/contracts/policies.py` holds `Expr`, `Policy`, `TablePolicies`, and the AST validator. We move the engine-relevant types to `shared/policies/ast.py`. The contracts file becomes a backward-compat re-export so call sites don't break in this commit; later tasks migrate imports.

- [ ] **Step 1: Write the failing test for `PolicyDocument`**

Append to: `api/tests/unit/policies/test_engine_protocols.py`

```python
from shared.policies.ast import (
    Expr,
    Policy,
    PolicyDocument,
)


def test_policy_document_round_trip():
    """PolicyDocument validates with the action vocab as plain strings."""
    doc = PolicyDocument.model_validate({
        "policies": [
            {
                "name": "admin",
                "actions": ["read", "write", "list", "delete"],
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


def test_expr_validator_still_works():
    """AST validator still rejects unknown user fields."""
    import pytest

    with pytest.raises(ValueError, match="unknown user field"):
        Expr.model_validate({"user": "not_a_real_field"})
```

- [ ] **Step 2: Run test to verify it fails (import error)**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py -v`
Expected: FAIL — `ImportError: shared.policies.ast`.

- [ ] **Step 3: Create `api/shared/policies/ast.py`**

Move the validator and types out of `api/src/models/contracts/policies.py`. The `Action` Literal is widened to `str` here (each domain re-types it).

Create: `api/shared/policies/ast.py`

```python
"""Pydantic AST types for policy expressions. Domain-agnostic.

The AST validator enforces structural correctness. Reference namespaces
(`{row: ...}`, `{file: ...}`) are validated by name against
KNOWN_USER_FIELDS for the user namespace; the entity namespace is
accepted opaquely — domain-specific allowlists live in the domain's
Resolver/Binding.
"""
from __future__ import annotations

from typing import Any, Final

from pydantic import (
    BaseModel,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from shared.policies.functions import FUNCTIONS

KNOWN_USER_FIELDS: Final[frozenset[str]] = frozenset({
    "user_id",
    "email",
    "organization_id",
    "is_platform_admin",
    "role_ids",
    "role_names",
})

_LOGIC_OPS: Final[frozenset[str]] = frozenset({"and", "or", "not"})
_COMPARE_OPS: Final[frozenset[str]] = frozenset({"eq", "neq", "lt", "lte", "gt", "gte"})
_OTHER_OPS: Final[frozenset[str]] = frozenset({"in", "is_null", "call"})
_ALL_OPS: Final[frozenset[str]] = _LOGIC_OPS | _COMPARE_OPS | _OTHER_OPS

_DEPTH_LIMIT: Final[int] = 64

# Reserved namespaces that are well-known to the AST validator.
# Domain-specific ones (e.g. "row", "file") are accepted opaquely if they
# are the only key in a node — the domain's Resolver enforces field validity.
_RESERVED_TOP_KEYS: Final[frozenset[str]] = frozenset({"user", "call", "args"}) | _ALL_OPS


def _validate_operand(node: Any, depth: int = 0, path: str = "$") -> None:
    if depth >= _DEPTH_LIMIT:
        raise ValueError(
            f"{path}: expression nested too deeply (>{_DEPTH_LIMIT} levels)"
        )
    if isinstance(node, (str, int, float, bool)) or node is None:
        return
    if isinstance(node, list):
        for i, item in enumerate(node):
            _validate_operand(item, depth + 1, f"{path}[{i}]")
        return
    if not isinstance(node, dict):
        raise ValueError(f"{path}: unexpected operand type: {type(node).__name__}")

    keys = set(node.keys())
    if keys == {"user"}:
        ref = node["user"]
        if ref not in KNOWN_USER_FIELDS:
            raise ValueError(
                f"{path}: unknown user field {ref!r}; "
                f"available: {sorted(KNOWN_USER_FIELDS)}"
            )
        return
    if keys == {"call", "args"} or keys == {"call"}:
        _validate_call(node, depth=depth, path=path)
        return
    if len(keys) == 1:
        op = next(iter(keys))
        if op in _ALL_OPS:
            _validate_op_node(op, node[op], depth=depth, path=path)
            return
        # Treat as a domain reference: `{ <namespace>: "path.path" }`.
        # We validate the *shape* here; the Resolver validates the field name.
        ref = node[op]
        if not isinstance(ref, str) or not ref:
            raise ValueError(
                f"{path}: {op!r} reference must be a non-empty string, got {ref!r}"
            )
        return
    raise ValueError(
        f"{path}: operator node must have exactly one key, got {sorted(keys)}"
    )


def _validate_op_node(op: str, value: Any, depth: int, path: str) -> None:
    if op in _LOGIC_OPS - {"not"}:
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError(f"{path}.{op}: {op} requires at least two operands")
        for i, item in enumerate(value):
            _validate_operand(item, depth + 1, f"{path}.{op}[{i}]")
        return
    if op == "not":
        if isinstance(value, list):
            raise ValueError(
                f"{path}.{op}: not requires exactly one operand (not a list)"
            )
        _validate_operand(value, depth + 1, f"{path}.{op}")
        return
    if op in _COMPARE_OPS:
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"{path}.{op}: {op} requires exactly two operands")
        if op in {"eq", "neq"}:
            for operand in value:
                if operand is None:
                    raise ValueError(
                        f"{path}.{op}: {op} does not accept null literals "
                        "(NULL semantics differ between evaluator and SQL "
                        "pushdown); use is_null instead"
                    )
        for i, item in enumerate(value):
            _validate_operand(item, depth + 1, f"{path}.{op}[{i}]")
        return
    if op == "in":
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"{path}.{op}: in requires [operand, [literal, ...]]")
        left, right = value
        _validate_operand(left, depth + 1, f"{path}.{op}[0]")
        if not isinstance(right, list) or not right:
            raise ValueError(
                f"{path}.{op}: in requires a non-empty literal list as second arg"
            )
        for i, item in enumerate(right):
            if not isinstance(item, (str, int, float, bool)) and item is not None:
                raise ValueError(
                    f"{path}.{op}[1][{i}]: in literal list items must be scalars or null"
                )
        return
    if op == "is_null":
        if isinstance(value, list):
            raise ValueError(
                f"{path}.{op}: is_null requires exactly one operand (not a list)"
            )
        _validate_operand(value, depth + 1, f"{path}.{op}")
        return


def _validate_call(node: dict, depth: int, path: str) -> None:
    target = node.get("call")
    args = node.get("args", [])
    if not isinstance(target, str):
        raise ValueError(f"{path}: call target must be a string")
    if target not in FUNCTIONS:
        raise ValueError(
            f"{path}: unknown function {target!r}; available: {sorted(FUNCTIONS)}"
        )
    fn = FUNCTIONS[target]
    if len(args) != len(fn.arg_types):
        raise ValueError(
            f"{path}: function {target!r} expects {len(fn.arg_types)} args, "
            f"got {len(args)}"
        )
    for i, (arg, t) in enumerate(zip(args, fn.arg_types)):
        if isinstance(arg, dict):
            _validate_operand(arg, depth + 1, f"{path}.args[{i}]")
            continue
        if not isinstance(arg, t):
            raise ValueError(
                f"{path}.args[{i}]: function {target!r} arg {i} expected "
                f"{t.__name__}, got {type(arg).__name__}"
            )


class Expr(RootModel[dict]):
    """Policy expression AST. Validated at construction."""

    @model_validator(mode="after")
    def _validate(self):
        _validate_operand(self.root, depth=0, path="$")
        return self


class Policy(BaseModel):
    """One rule in a policy document. `actions` is a list of strings;
    each domain re-types via Literal for stricter validation at its
    routers (e.g. `TablePolicy.actions: list[Literal["read","create",...]]`).
    """

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    actions: list[str] = Field(min_length=1)
    when: Expr | None = None

    @field_validator("actions")
    @classmethod
    def _no_dup_actions(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("actions must not contain duplicates")
        return v


class PolicyDocument(BaseModel):
    """Container for a list of rules. Resolution is additive OR per action."""

    policies: list[Policy] = Field(default_factory=list)
```

- [ ] **Step 4: Convert `contracts/policies.py` to a re-export shim**

Modify: `api/src/models/contracts/policies.py`

Replace entire content with:

```python
"""Backward-compat shim. Use `shared.policies.ast` for engine types.

Tables-specific imports re-exported here. New code should import from
`shared.table_policies` (TablePolicies alias) or `shared.policies.ast`
(PolicyDocument).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from shared.policies.ast import (
    KNOWN_USER_FIELDS,
    Expr,
    Policy,
    PolicyDocument,
)

# Table-specific action vocab. File-policies has its own.
Action = Literal["read", "create", "update", "delete"]


# TablePolicies is a tables-typed alias of PolicyDocument. Kept here for the
# few callers (tests, validate endpoint) that import it from this path.
TablePolicies = PolicyDocument


class PolicyValidationError(BaseModel):
    """Single structured validation error for a policy document."""

    path: str
    message: str


class PolicyValidationResponse(BaseModel):
    """Outcome of a POST /api/tables/policies/validate call."""

    ok: bool
    errors: list[PolicyValidationError] = Field(default_factory=list)


__all__ = [
    "KNOWN_USER_FIELDS",
    "Action",
    "Expr",
    "Policy",
    "PolicyDocument",
    "TablePolicies",
    "PolicyValidationError",
    "PolicyValidationResponse",
]
```

- [ ] **Step 5: Run the engine-protocols tests**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py -v`
Expected: PASS — all four tests green.

- [ ] **Step 6: Run the full policies test suite to confirm no regressions**

Run: `./test.sh tests/unit/policies/ -v`
Expected: PASS — all tests still green (count matches Task 1 baseline).

- [ ] **Step 7: Commit**

```bash
git add api/shared/policies/ast.py api/src/models/contracts/policies.py api/tests/unit/policies/test_engine_protocols.py
git commit -m "refactor(policies): extract AST types into shared.policies.ast

Move Expr/Policy/PolicyDocument and the AST validator out of
src/models/contracts/policies.py and into shared/policies/ast.py.
The contracts file remains as a re-export shim so existing imports
keep working; subsequent commits migrate them.

Action is widened to list[str] in the shared Policy class; each
domain (tables, files) re-types it via Literal at its router boundary."
```

---

## Task 4: Make `evaluate.py` Resolver-driven

**Files:**
- Modify: `api/shared/policies/evaluate.py`
- Modify: `api/tests/unit/policies/test_evaluate.py`

- [ ] **Step 1: Write the failing test for resolver-driven evaluate**

Append to: `api/tests/unit/policies/test_engine_protocols.py`

```python
from shared.policies.ast import Expr
from shared.policies.evaluate import evaluate


class _U:
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
    assert evaluate(expr, ctx={"owner_id": "u-1"}, user=_U(), resolver=resolver) is True
    assert evaluate(expr, ctx={"owner_id": "u-2"}, user=_U(), resolver=resolver) is False


def test_evaluate_with_alternate_namespace():
    """A different-namespace resolver handles its own references."""

    class FileResolverStub:
        namespace = "file"

        def resolve(self, path: str, ctx: Any) -> Any:
            return (ctx or {}).get(path)

    expr = Expr.model_validate({"eq": [{"file": "created_by"}, {"user": "user_id"}]})
    resolver = FileResolverStub()
    assert evaluate(expr, ctx={"created_by": "u-1"}, user=_U(), resolver=resolver) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py::test_evaluate_with_stub_resolver -v`
Expected: FAIL — `evaluate()` does not yet accept a `resolver` kwarg.

- [ ] **Step 3: Rewrite `evaluate.py` to be Resolver-driven**

Replace: `api/shared/policies/evaluate.py`

```python
"""Pure-function policy evaluator. Domain-agnostic.

Reference resolution is delegated to a Resolver. The walker only knows
about literals, the `{user: ...}` namespace, `{call: ...}`, operators,
and "everything else is a domain reference handled by the Resolver".
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from shared.policies.ast import Expr
from shared.policies.functions import FUNCTIONS
from shared.policies.resolver import Resolver


def evaluate(expr: Expr, ctx: Any, user: Any, resolver: Resolver) -> bool:
    """Evaluate an expression against a domain ctx + user, return bool."""
    return bool(_eval_node(expr.root, ctx, user, resolver))


def _eval_node(node: Any, ctx: Any, user: Any, resolver: Resolver) -> Any:
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    if isinstance(node, list):
        return [_eval_node(item, ctx, user, resolver) for item in node]

    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {"user"}:
            return _resolve_user_field(user, node["user"])
        if keys == {resolver.namespace}:
            return resolver.resolve(node[resolver.namespace], ctx)
        if "call" in keys:
            return _eval_call(node, ctx, user, resolver)
        if len(keys) == 1:
            op = next(iter(keys))
            return _eval_op(op, node[op], ctx, user, resolver)

    raise ValueError(f"unevaluatable node: {node!r}")


def _resolve_user_field(user: Any, field: str) -> Any:
    val = getattr(user, field, None)
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, list):
        return [str(v) if isinstance(v, UUID) else v for v in val]
    return val


def _eval_call(node: dict, ctx: Any, user: Any, resolver: Resolver) -> bool:
    target = node["call"]
    args = [_eval_node(a, ctx, user, resolver) for a in node.get("args", [])]
    fn = FUNCTIONS[target]
    return fn.evaluate(args, user, ctx if isinstance(ctx, dict) else {})


def _eval_op(op: str, value: Any, ctx: Any, user: Any, resolver: Resolver) -> bool:
    if op == "and":
        for item in value:
            if not _eval_node(item, ctx, user, resolver):
                return False
        return True
    if op == "or":
        for item in value:
            if _eval_node(item, ctx, user, resolver):
                return True
        return False
    if op == "not":
        return not _eval_node(value, ctx, user, resolver)
    if op == "eq":
        return _scalar_eq(
            _eval_node(value[0], ctx, user, resolver),
            _eval_node(value[1], ctx, user, resolver),
        )
    if op == "neq":
        return not _scalar_eq(
            _eval_node(value[0], ctx, user, resolver),
            _eval_node(value[1], ctx, user, resolver),
        )
    if op in ("lt", "lte", "gt", "gte"):
        a = _eval_node(value[0], ctx, user, resolver)
        b = _eval_node(value[1], ctx, user, resolver)
        if a is None or b is None:
            return False
        try:
            if op == "lt":
                return a < b
            if op == "lte":
                return a <= b
            if op == "gt":
                return a > b
            if op == "gte":
                return a >= b
        except TypeError:
            return False
    if op == "in":
        a = _eval_node(value[0], ctx, user, resolver)
        if a is None:
            return False
        return a in value[1]
    if op == "is_null":
        return _eval_node(value, ctx, user, resolver) is None
    raise ValueError(f"unknown operator {op!r}")


def _scalar_eq(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    return a == b
```

- [ ] **Step 4: Migrate existing `test_evaluate.py` to pass a Resolver**

Modify: `api/tests/unit/policies/test_evaluate.py`

Find every call to `evaluate(expr, row=..., user=...)` and update to `evaluate(expr, ctx=..., user=..., resolver=RowResolver())`. Add at the top:

```python
from shared.table_policies import RowResolver
```

Note: `RowResolver` doesn't exist yet — Task 6 creates it. Use a local stub in this file *for now*:

```python
# At top of test file, before any test:
class _RowResolverForTest:
    namespace = "row"
    def resolve(self, path, ctx):
        # Mirror the old _resolve_row_path semantics: dot-paths against the dict
        parts = path.split(".")
        cur = ctx
        for p in parts:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
            if cur is None:
                return None
        return cur
```

Then in every test, the call becomes:

```python
result = evaluate(expr, ctx=row, user=user, resolver=_RowResolverForTest())
```

(After Task 6 lands `RowResolver`, Task 7 replaces this stub with the real import.)

- [ ] **Step 5: Run engine-protocols tests**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py -v`
Expected: PASS — all six tests green.

- [ ] **Step 6: Run test_evaluate.py**

Run: `./test.sh tests/unit/policies/test_evaluate.py -v`
Expected: PASS — all tests green.

- [ ] **Step 7: Commit**

```bash
git add api/shared/policies/evaluate.py api/tests/unit/policies/test_evaluate.py api/tests/unit/policies/test_engine_protocols.py
git commit -m "refactor(policies): make evaluate.py Resolver-driven

Reference resolution moves behind the Resolver protocol. The walker no
longer hardcodes the 'row' namespace. The {user: ...} namespace and
function dispatch stay in the walker (domain-agnostic). Tests use a
local _RowResolverForTest stub; a follow-up commit replaces it with
the real RowResolver from shared.table_policies."
```

---

## Task 5: Make `compile.py` Binding-driven

**Files:**
- Modify: `api/shared/policies/compile.py`
- Modify: `api/tests/unit/policies/test_compile.py`

- [ ] **Step 1: Write the failing test for Binding-driven compile**

Append to: `api/tests/unit/policies/test_engine_protocols.py`

```python
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import ColumnElement

from shared.policies.compile import compile_to_sql

_Base = declarative_base()


class _Doc(_Base):
    __tablename__ = "_test_doc"
    id = Column(Integer, primary_key=True)
    owner_id = Column(String)


class _StubBinding:
    namespace = "row"

    def resolve_reference(self, path: str) -> ColumnElement[Any]:
        col = getattr(_Doc, path, None)
        if col is None:
            raise ValueError(f"unknown column: {path}")
        return col


def test_compile_with_stub_binding():
    """compile_to_sql delegates row-reference resolution to the Binding."""
    expr = Expr.model_validate({"eq": [{"row": "owner_id"}, {"user": "user_id"}]})
    sql = compile_to_sql(expr, user=_U(), binding=_StubBinding())
    # Compile to a string just to assert the right column shows up
    s = str(sql.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_id" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py::test_compile_with_stub_binding -v`
Expected: FAIL — `compile_to_sql` does not yet accept a `binding` kwarg.

- [ ] **Step 3: Rewrite `compile.py` to be Binding-driven**

Replace: `api/shared/policies/compile.py`

```python
"""SQL compiler for policy expressions. Domain-agnostic.

Reference resolution at compile time is delegated to a Binding. The compiler
walker knows literals, the `{user: ...}` namespace, `{call: ...}`, and
operators; everything else under a single-key dict is treated as a domain
reference and dispatched to the Binding.

User-side facts and function calls are resolved at compile time. The
resulting SQL contains only parameterized literals against the columns the
Binding produces.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_ as sa_and
from sqlalchemy import false as sa_false
from sqlalchemy import literal
from sqlalchemy import not_ as sa_not
from sqlalchemy import or_ as sa_or
from sqlalchemy import true as sa_true
from sqlalchemy.sql import ColumnElement

from shared.policies.ast import Expr
from shared.policies.binding import Binding
from shared.policies.functions import FUNCTIONS


def compile_to_sql(expr: Expr, user: Any, binding: Binding) -> ColumnElement[Any]:
    """Compile an Expr to a SQLAlchemy boolean expression for the binding's domain."""
    return _compile_node(expr.root, user, binding)


def _compile_node(node: Any, user: Any, binding: Binding) -> ColumnElement[Any]:
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {"user"}:
            return _resolve_user_to_literal(user, node["user"])
        if keys == {binding.namespace}:
            return binding.resolve_reference(node[binding.namespace])
        if "call" in keys:
            return _compile_call(node, user)
        if len(keys) == 1:
            op = next(iter(keys))
            return _compile_op(op, node[op], user, binding)
    if isinstance(node, (str, int, float, bool)) or node is None:
        return literal(node)
    raise ValueError(f"unrendable node: {node!r}")


def _resolve_user_to_literal(user: Any, field: str) -> ColumnElement[Any]:
    val = getattr(user, field, None)
    if isinstance(val, UUID):
        val = str(val)
    return literal(val)


def _compile_call(node: dict, user: Any) -> ColumnElement[Any]:
    target = node["call"]
    args = [_resolve_arg_for_call(a, user) for a in node.get("args", [])]
    fn = FUNCTIONS[target]
    result = fn.compile(args, user)
    return sa_true() if result else sa_false()


def _resolve_arg_for_call(arg: Any, user: Any) -> Any:
    if isinstance(arg, dict):
        keys = set(arg.keys())
        if keys == {"user"}:
            return getattr(user, arg["user"], None)
        raise ValueError(
            f"function call args must be literals or {{user: ...}}; "
            f"got {arg!r} which the SQL compiler cannot resolve"
        )
    return arg


def _compile_op(op: str, value: Any, user: Any, binding: Binding) -> ColumnElement[Any]:
    if op == "and":
        return sa_and(*(_compile_node(item, user, binding) for item in value))
    if op == "or":
        return sa_or(*(_compile_node(item, user, binding) for item in value))
    if op == "not":
        return sa_not(_compile_node(value, user, binding).self_group())
    if op == "eq":
        return _compile_node(value[0], user, binding) == _compile_node(value[1], user, binding)
    if op == "neq":
        return _compile_node(value[0], user, binding) != _compile_node(value[1], user, binding)
    if op == "lt":
        return _compile_node(value[0], user, binding) < _compile_node(value[1], user, binding)
    if op == "lte":
        return _compile_node(value[0], user, binding) <= _compile_node(value[1], user, binding)
    if op == "gt":
        return _compile_node(value[0], user, binding) > _compile_node(value[1], user, binding)
    if op == "gte":
        return _compile_node(value[0], user, binding) >= _compile_node(value[1], user, binding)
    if op == "in":
        left = _compile_node(value[0], user, binding)
        return left.in_(value[1])
    if op == "is_null":
        return _compile_node(value, user, binding).is_(None)
    raise ValueError(f"unknown operator {op!r}")
```

- [ ] **Step 4: Migrate `test_compile.py` to pass a Binding**

Modify: `api/tests/unit/policies/test_compile.py`

Add at top:

```python
from shared.policies.binding import Binding
from sqlalchemy.sql import ColumnElement
from src.models.orm.tables import Document


# Inline TableBinding so this test doesn't depend on Task 6.
_COLUMN_MAPPED_ROW_FIELDS = {
    "id": Document.id,
    "organization_id": None,
    "created_by": Document.created_by,
    "updated_by": Document.updated_by,
    "created_at": Document.created_at,
    "updated_at": Document.updated_at,
    "table_id": Document.table_id,
}


class _TableBindingForTest:
    namespace = "row"

    def resolve_reference(self, path: str) -> ColumnElement[Any]:
        parts = path.split(".")
        if len(parts) == 1 and parts[0] in _COLUMN_MAPPED_ROW_FIELDS:
            col = _COLUMN_MAPPED_ROW_FIELDS[parts[0]]
            if col is not None:
                return col
        if len(parts) == 1:
            return Document.data[parts[0]].astext
        return Document.data[parts].astext
```

Replace every `compile_to_sql(expr, user)` call with `compile_to_sql(expr, user, _TableBindingForTest())`.

- [ ] **Step 5: Run engine-protocols tests**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py -v`
Expected: PASS — all seven tests green.

- [ ] **Step 6: Run test_compile.py**

Run: `./test.sh tests/unit/policies/test_compile.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/shared/policies/compile.py api/tests/unit/policies/test_compile.py api/tests/unit/policies/test_engine_protocols.py
git commit -m "refactor(policies): make compile.py Binding-driven

compile_to_sql now takes a Binding that knows how to project AST
references to SQLAlchemy columns. The walker no longer imports
Document or _COLUMN_MAPPED_ROW_FIELDS. Tests inline a _TableBindingForTest
locally; a follow-up commit replaces it with the real TableBinding."
```

---

## Task 6: Create `shared/table_policies.py` with RowResolver, TableBinding, and table-specific helpers

**Files:**
- Create: `api/shared/table_policies.py`
- Modify: `api/shared/policies/probe.py` (drop `compile_read_filter` and `make_seed_admin_bypass`)
- Modify: `api/shared/policies/subscription.py` (drop hardcoded `TablePolicies`, accept Resolver)

- [ ] **Step 1: Write the failing test for `RowResolver` and `TableBinding`**

Create: `api/tests/unit/policies/test_table_policies_binding.py`

```python
"""Tests for the table-domain bindings to the shared engine."""
from __future__ import annotations

from typing import Any

from sqlalchemy.sql import ColumnElement

from shared.policies.ast import Expr, Policy, PolicyDocument
from shared.table_policies import (
    RowResolver,
    TableBinding,
    compile_read_filter,
    make_seed_admin_bypass,
)
from src.models.orm.tables import Document


class _U:
    user_id = "u-1"
    email = "u@x"
    organization_id = None
    is_platform_admin = True
    role_ids: list = []
    role_names: list = []


def test_row_resolver_namespace():
    assert RowResolver().namespace == "row"


def test_row_resolver_dot_path():
    r = RowResolver()
    assert r.resolve("a", {"a": 1}) == 1
    assert r.resolve("a.b", {"a": {"b": 2}}) == 2
    assert r.resolve("missing", {}) is None
    assert r.resolve("a.b", None) is None


def test_table_binding_column_mapped():
    b = TableBinding()
    col = b.resolve_reference("created_by")
    assert col is Document.created_by


def test_table_binding_jsonb_fallback():
    b = TableBinding()
    col = b.resolve_reference("data_field")
    # The JSONB path renders to the documents.data->>'data_field' style;
    # asserting the column references Document.data is enough.
    s = str(col)
    assert "data" in s.lower()


def test_make_seed_admin_bypass_uses_table_actions():
    seed = make_seed_admin_bypass()
    assert seed["policies"][0]["actions"] == ["read", "create", "update", "delete"]


def test_compile_read_filter_returns_none_for_empty():
    doc = PolicyDocument()
    assert compile_read_filter(doc, user=_U()) is None


def test_compile_read_filter_or_across_rules():
    doc = PolicyDocument.model_validate({
        "policies": [
            {"name": "a", "actions": ["read"], "when": {"user": "is_platform_admin"}},
            {"name": "b", "actions": ["read"], "when": {"eq": [{"row": "owner_id"}, {"user": "user_id"}]}},
        ],
    })
    sql = compile_read_filter(doc, user=_U())
    assert sql is not None
    assert isinstance(sql, ColumnElement)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/policies/test_table_policies_binding.py -v`
Expected: FAIL — `ModuleNotFoundError: shared.table_policies`.

- [ ] **Step 3: Create `shared/table_policies.py`**

Create: `api/shared/table_policies.py`

```python
"""Table-domain bindings for the shared policy engine.

This module is the only thing that knows about `Document`, the `_COLUMN_MAPPED_ROW_FIELDS`
mapping, the table action vocab (`read`, `create`, `update`, `delete`), and the
seeded admin-bypass shape for tables. The shared engine in `shared/policies/`
consumes these via the Resolver and Binding protocols.
"""
from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import or_ as sa_or
from sqlalchemy import true as sa_true
from sqlalchemy.sql import ColumnElement

from shared.policies.ast import PolicyDocument
from shared.policies.compile import compile_to_sql
from src.models.orm.tables import Document

# Re-export so handlers can import the tables-typed name.
TablePolicies = PolicyDocument

# Column-mapped row references — read from the SQL column, not JSONB.
_COLUMN_MAPPED_ROW_FIELDS: dict[str, Any] = {
    "id": Document.id,
    "organization_id": None,  # documents has no organization_id; comes from join
    "created_by": Document.created_by,
    "updated_by": Document.updated_by,
    "created_at": Document.created_at,
    "updated_at": Document.updated_at,
    "table_id": Document.table_id,
}

# NOTE on `organization_id`: documents are scoped via their parent table.
# When the compiler is invoked from a query handler, the handler already
# applies a `Table.organization_id` filter at the join. References to
# `row.organization_id` in policies fall through to the data JSONB lookup
# (`data->>'organization_id'`) — apps that need this should denormalize
# the org id into the row's data JSONB at insert time.


class RowResolver:
    """Resolves `{row: path}` references against a Document row dict."""

    namespace: ClassVar[str] = "row"

    def resolve(self, path: str, ctx: Any) -> Any:
        parts = path.split(".")
        cur: Any = ctx
        for part in parts:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
            if cur is None:
                return None
        return cur


class TableBinding:
    """Resolves `{row: path}` references to SQLAlchemy columns on Document."""

    namespace: ClassVar[str] = "row"

    def resolve_reference(self, path: str) -> ColumnElement[Any]:
        parts = path.split(".")
        if len(parts) == 1 and parts[0] in _COLUMN_MAPPED_ROW_FIELDS:
            col = _COLUMN_MAPPED_ROW_FIELDS[parts[0]]
            if col is not None:
                return col
        if len(parts) == 1:
            return Document.data[parts[0]].astext
        return Document.data[parts].astext


def compile_read_filter(
    policies: PolicyDocument,
    user: Any,
) -> ColumnElement[Any] | None:
    """Compile the OR of all read-allowing rules into a single WHERE clause.

    Returns None if no policy grants read (the handler must deny). Table-specific
    because files have no SQL pushdown.
    """
    binding = TableBinding()
    fragments: list[ColumnElement[Any]] = []
    for policy in policies.policies:
        if "read" not in policy.actions:
            continue
        if policy.when is None:
            fragments.append(sa_true())
            continue
        fragments.append(compile_to_sql(policy.when, user, binding))
    if not fragments:
        return None
    if len(fragments) == 1:
        return fragments[0]
    return sa_or(*fragments)


def make_seed_admin_bypass() -> dict:
    """Seeded policy for a freshly-created table. Table-specific action vocab."""
    return {
        "policies": [
            {
                "name": "admin_bypass",
                "description": "Platform admins bypass all checks. Edit or delete to enforce stricter audit.",
                "actions": ["read", "create", "update", "delete"],
                "when": {"user": "is_platform_admin"},
            },
        ],
    }


__all__ = [
    "RowResolver",
    "TableBinding",
    "TablePolicies",
    "compile_read_filter",
    "make_seed_admin_bypass",
]
```

- [ ] **Step 4: Update `probe.py` — drop the moved functions; update remaining signatures to accept Resolver**

Replace: `api/shared/policies/probe.py`

```python
"""Action-level policy helpers. Domain-agnostic.

`evaluate_action` and `is_subscribe_authorized` walk the rules in a
PolicyDocument and dispatch to `evaluate` (which itself dispatches to a
Resolver). Domain-specific helpers (e.g. `compile_read_filter`,
`make_seed_admin_bypass`) live in the domain module.
"""
from __future__ import annotations

from typing import Any

from shared.policies.ast import PolicyDocument
from shared.policies.evaluate import evaluate
from shared.policies.resolver import Resolver


def evaluate_action(
    action: str,
    policies: PolicyDocument,
    ctx: Any,
    user: Any,
    resolver: Resolver,
) -> bool:
    """OR across all rules whose `actions` includes `action`. Default deny."""
    for policy in policies.policies:
        if action not in policy.actions:
            continue
        if policy.when is None:
            return True
        if evaluate(policy.when, ctx=ctx, user=user, resolver=resolver):
            return True
    return False


def is_subscribe_authorized(
    policies: PolicyDocument,
    user: Any,
    resolver: Resolver,
) -> bool:
    """Probe: would ANY read message ever reach this user?"""
    for policy in policies.policies:
        if "read" not in policy.actions:
            continue
        if policy.when is None:
            return True
        if _is_purely_user_dependent(policy.when.root, resolver):
            if evaluate(policy.when, ctx={}, user=user, resolver=resolver):
                return True
            continue
        return True
    return False


def _is_purely_user_dependent(node: Any, resolver: Resolver) -> bool:
    """True if the expression references only USER fields and literals."""
    if isinstance(node, (str, int, float, bool)) or node is None:
        return True
    if isinstance(node, list):
        return all(_is_purely_user_dependent(x, resolver) for x in node)
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {resolver.namespace}:
            return False
        if keys == {"user"}:
            return True
        if "call" in keys:
            return all(_is_purely_user_dependent(a, resolver) for a in node.get("args", []))
        if len(keys) == 1:
            return _is_purely_user_dependent(node[next(iter(keys))], resolver)
    return False
```

- [ ] **Step 5: Update `subscription.py` to accept Resolver**

Replace: `api/shared/policies/subscription.py`

```python
"""Per-message visibility decision for subscriptions.

Domain-agnostic. The four-way visibility transition table
(old_visible × new_visible → emitted action) is the same for any
domain that ships row-like change events.
"""
from __future__ import annotations

from typing import Any, Literal

from shared.policies.ast import Expr, PolicyDocument
from shared.policies.evaluate import evaluate
from shared.policies.probe import evaluate_action
from shared.policies.resolver import Resolver

Action = Literal["insert", "update", "delete"]


def is_row_visible(
    ctx: dict | None,
    policies: PolicyDocument,
    user: Any,
    resolver: Resolver,
    user_filter: Expr | None = None,
) -> bool:
    """True iff ctx is readable AND passes the user-supplied filter."""
    if ctx is None:
        return False
    if not evaluate_action("read", policies, ctx, user, resolver):
        return False
    if user_filter is not None and not evaluate(user_filter, ctx=ctx, user=user, resolver=resolver):
        return False
    return True


def decide_visibility_change(
    old_ctx: dict | None,
    new_ctx: dict | None,
    policies: PolicyDocument,
    user: Any,
    resolver: Resolver,
    user_filter: Expr | None = None,
) -> tuple[Action, dict | str | None] | None:
    """Compute the four-way fanout decision."""
    old_visible = is_row_visible(old_ctx, policies, user, resolver, user_filter)
    new_visible = is_row_visible(new_ctx, policies, user, resolver, user_filter)

    if not old_visible and not new_visible:
        return None
    if not old_visible and new_visible:
        return ("insert", new_ctx)
    if old_visible and not new_visible:
        return ("delete", (old_ctx or {}).get("id"))
    return ("update", new_ctx)
```

- [ ] **Step 6: Run the new table-binding test**

Run: `./test.sh tests/unit/policies/test_table_policies_binding.py -v`
Expected: PASS — all seven tests green.

- [ ] **Step 7: Run the full policies suite — expect import failures in other tests/handlers (we fix in Tasks 7–9)**

Run: `./test.sh tests/unit/policies/ -v 2>&1 | tail -20`
Expected: Some import failures in `test_probe.py`, `test_subscription_logic.py`, `test_round_trip.py` — they still call old signatures. Those are addressed in Task 7. Do not commit yet.

- [ ] **Step 8: Migrate `test_probe.py` to new signatures**

Modify: `api/tests/unit/policies/test_probe.py`

Replace the `make_seed_admin_bypass` import line:

```python
# OLD
from shared.policies.probe import (
    evaluate_action,
    compile_read_filter,
    is_subscribe_authorized,
    make_seed_admin_bypass,
)
# NEW
from shared.policies.probe import (
    evaluate_action,
    is_subscribe_authorized,
)
from shared.table_policies import (
    RowResolver,
    compile_read_filter,
    make_seed_admin_bypass,
)
```

Then update every call to pass `resolver=RowResolver()`:

```python
# OLD
evaluate_action("read", policies, row, user)
# NEW
evaluate_action("read", policies, row, user, resolver=RowResolver())

# OLD
is_subscribe_authorized(policies, user)
# NEW
is_subscribe_authorized(policies, user, resolver=RowResolver())
```

`compile_read_filter` signature is unchanged from the test's perspective (still `(policies, user)`).

- [ ] **Step 9: Migrate `test_subscription_logic.py`**

Modify: `api/tests/unit/policies/test_subscription_logic.py`

Add import:

```python
from shared.table_policies import RowResolver
```

Update every call site:

```python
# OLD
decide_visibility_change(old_row, new_row, policies, user)
# NEW
decide_visibility_change(old_row, new_row, policies, user, resolver=RowResolver())
```

If `user_filter=` is supplied, keep it.

- [ ] **Step 10: Migrate `test_round_trip.py`**

Modify: `api/tests/unit/policies/test_round_trip.py`

Add imports:

```python
from shared.table_policies import RowResolver, TableBinding
```

Update every call:

```python
# OLD
evaluate(expr, row=row, user=user)
compile_to_sql(expr, user)
# NEW
evaluate(expr, ctx=row, user=user, resolver=RowResolver())
compile_to_sql(expr, user, binding=TableBinding())
```

- [ ] **Step 11: Migrate `test_evaluate.py` to use the real `RowResolver`**

Modify: `api/tests/unit/policies/test_evaluate.py`

Remove the inline `_RowResolverForTest` class. Replace with:

```python
from shared.table_policies import RowResolver
```

Update every test that referenced `_RowResolverForTest()` to use `RowResolver()`.

- [ ] **Step 12: Migrate `test_compile.py` to use the real `TableBinding`**

Modify: `api/tests/unit/policies/test_compile.py`

Remove the inline `_TableBindingForTest`. Replace with:

```python
from shared.table_policies import TableBinding
```

Update every call to use `TableBinding()`.

- [ ] **Step 13: Run the full policies suite**

Run: `./test.sh tests/unit/policies/ -v`
Expected: PASS — test count ≥ baseline from Task 1.

- [ ] **Step 14: Commit**

```bash
git add api/shared/table_policies.py api/shared/policies/probe.py api/shared/policies/subscription.py api/tests/unit/policies/test_table_policies_binding.py api/tests/unit/policies/test_probe.py api/tests/unit/policies/test_subscription_logic.py api/tests/unit/policies/test_round_trip.py api/tests/unit/policies/test_evaluate.py api/tests/unit/policies/test_compile.py
git commit -m "refactor(policies): extract RowResolver/TableBinding into shared.table_policies

Move table-specific code out of the engine:
- RowResolver + TableBinding (Protocol implementations)
- compile_read_filter (SQL-pushdown helper, table-specific)
- make_seed_admin_bypass (table action vocab)

probe.py drops compile_read_filter and make_seed_admin_bypass and takes
a Resolver parameter on evaluate_action / is_subscribe_authorized.
subscription.py takes a Resolver. All policies tests migrate to the
new signatures and import paths."
```

---

## Task 7: Migrate `routers/tables.py` to new imports + signatures

**Files:**
- Modify: `api/src/routers/tables.py`

- [ ] **Step 1: Confirm current call sites**

Run: `grep -n "compile_read_filter\|evaluate_action\|make_seed_admin_bypass" api/src/routers/tables.py`
Expected output: lines 24, 25, 145, 159, 1130, 1256, 1323, 1334, 1421 (approximately).

- [ ] **Step 2: Rewrite the imports**

Modify: `api/src/routers/tables.py`

Find the import block:

```python
from shared.policies.probe import (
    compile_read_filter,
    evaluate_action,
    is_subscribe_authorized,
    make_seed_admin_bypass,
)
```

(The actual import lines may include `compile_read_filter`, `make_seed_admin_bypass`, etc. — confirm by reading lines 23–27.)

Replace with:

```python
from shared.policies.probe import evaluate_action
from shared.table_policies import (
    RowResolver,
    TablePolicies,
    compile_read_filter,
    make_seed_admin_bypass,
)
```

Also remove `TablePolicies` from the `from src.models.contracts.policies import (...)` block (it's now imported from `shared.table_policies`). Keep `PolicyValidationError` / `PolicyValidationResponse` imports from `contracts/policies` if they appear.

- [ ] **Step 3: Update every call to `evaluate_action`**

Use sed-style search-and-replace, then manually verify. Each call needs `resolver=RowResolver()` appended. Pattern:

```python
# OLD
evaluate_action(action, policies, row, user)
# NEW
evaluate_action(action, policies, row, user, resolver=RowResolver())
```

Search:

```bash
grep -n "evaluate_action" api/src/routers/tables.py
```

For each line, add `, resolver=RowResolver()` as the last argument.

`compile_read_filter` calls do **not** need to change — the signature `(policies, user)` is unchanged.

- [ ] **Step 4: Run pyright on the file**

Run: `cd api && pyright src/routers/tables.py`
Expected: 0 errors.

- [ ] **Step 5: Run table E2E tests**

Run: `./test.sh tests/e2e/platform/test_table_policies_rest.py -v`
Expected: PASS — table behavior unchanged.

- [ ] **Step 6: Run the full table test suite to confirm no regressions**

Run: `./test.sh tests/e2e/platform/ -k "table" -v`
Expected: PASS — all table-related E2E tests still green.

- [ ] **Step 7: Commit**

```bash
git add api/src/routers/tables.py
git commit -m "refactor(tables): switch router to shared.table_policies imports

Move policy helper imports from shared.policies.probe to
shared.table_policies. Pass RowResolver() into every evaluate_action
call site. No behavior change."
```

---

## Task 8: Migrate `routers/websocket.py` and other `make_seed_admin_bypass` consumers

**Files:**
- Modify: `api/src/routers/websocket.py`
- Modify: `api/src/routers/cli.py`
- Modify: `api/src/services/manifest_import.py`
- Modify: `api/src/services/mcp_server/tools/tables.py`
- Modify: `api/src/routers/export_import.py`
- Modify: `api/tests/unit/test_admin_bypass_seed_migration.py`

- [ ] **Step 1: Migrate websocket.py**

Find the imports and call sites:

```bash
grep -n "is_subscribe_authorized\|decide_visibility_change\|make_seed_admin_bypass" api/src/routers/websocket.py
```

Update imports:

```python
# OLD
from shared.policies.probe import is_subscribe_authorized
from shared.policies.subscription import decide_visibility_change
# NEW
from shared.policies.probe import is_subscribe_authorized
from shared.policies.subscription import decide_visibility_change
from shared.table_policies import RowResolver
```

Pass `resolver=RowResolver()` to every `is_subscribe_authorized(...)` and `decide_visibility_change(...)` call.

- [ ] **Step 2: Migrate cli.py, manifest_import.py, mcp_server/tools/tables.py, export_import.py**

Each of these currently does `from shared.policies.probe import make_seed_admin_bypass`. Change to:

```python
from shared.table_policies import make_seed_admin_bypass
```

Run for each:

```bash
grep -rn "from shared.policies.probe import make_seed_admin_bypass" api/src/
```

Update every match.

- [ ] **Step 3: Migrate test_admin_bypass_seed_migration.py**

Modify: `api/tests/unit/test_admin_bypass_seed_migration.py`

```python
# OLD
from shared.policies.probe import make_seed_admin_bypass
# NEW
from shared.table_policies import make_seed_admin_bypass
```

Find the `evaluate_action` import:

```python
# OLD
from shared.policies.probe import evaluate_action
# NEW
from shared.policies.probe import evaluate_action
from shared.table_policies import RowResolver
```

Add `, resolver=RowResolver()` to every `evaluate_action(...)` call.

- [ ] **Step 4: Confirm no stragglers**

Run: `grep -rn "from shared.policies.probe import" api/`
Expected: every remaining match imports only `evaluate_action` and/or `is_subscribe_authorized` (never `make_seed_admin_bypass` or `compile_read_filter`).

- [ ] **Step 5: Pyright + ruff**

Run: `cd api && pyright && ruff check .`
Expected: 0 errors.

- [ ] **Step 6: Run unit test suite**

Run: `./test.sh unit -v 2>&1 | tail -30`
Expected: PASS.

- [ ] **Step 7: Run table-related E2E suite**

Run: `./test.sh tests/e2e/platform/ -k "table or websocket" -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add api/src/routers/websocket.py api/src/routers/cli.py api/src/services/manifest_import.py api/src/services/mcp_server/tools/tables.py api/src/routers/export_import.py api/tests/unit/test_admin_bypass_seed_migration.py
git commit -m "refactor(policies): migrate remaining make_seed_admin_bypass imports

Every caller now imports make_seed_admin_bypass from shared.table_policies
(not shared.policies.probe). websocket.py passes RowResolver() into
is_subscribe_authorized and decide_visibility_change."
```

---

## Task 9: Verify the engine is genuinely domain-agnostic (proof test)

**Files:**
- Modify: `api/tests/unit/policies/test_engine_protocols.py`

This task adds an end-to-end proof that the engine never imports table-specific code.

- [ ] **Step 1: Add an import-isolation test**

Append to: `api/tests/unit/policies/test_engine_protocols.py`

```python
def test_engine_does_not_import_domain_code():
    """The shared engine modules must not import any table-specific code.

    Static analysis: parse each engine module and assert nothing under
    `shared.policies.` imports `src.models.orm`, `shared.table_policies`,
    or any other table-specific surface.
    """
    import ast
    import pathlib

    engine_root = pathlib.Path("/app/shared/policies")
    forbidden_prefixes = (
        "src.models.orm",
        "shared.table_policies",
        "shared.file_policies",
    )

    bad: list[str] = []
    for py in engine_root.rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(p) for p in forbidden_prefixes):
                    bad.append(f"{py.name}: imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(p) for p in forbidden_prefixes):
                        bad.append(f"{py.name}: imports {alias.name}")

    assert not bad, "engine reaches into domain code:\n" + "\n".join(bad)
```

Note: the path `/app/shared/policies` is the container path (test stack mounts `/app` to `api/`). If running tests outside the stack returns a different path, the test loader (`./test.sh`) handles this — the file is read from the container's filesystem at runtime.

- [ ] **Step 2: Run the proof test**

Run: `./test.sh tests/unit/policies/test_engine_protocols.py::test_engine_does_not_import_domain_code -v`
Expected: PASS — no forbidden imports.

- [ ] **Step 3: Run the entire policies suite**

Run: `./test.sh tests/unit/policies/ -v`
Expected: PASS — all tests green, count ≥ Task 1 baseline + new tests.

- [ ] **Step 4: Commit**

```bash
git add api/tests/unit/policies/test_engine_protocols.py
git commit -m "test(policies): assert shared engine has no domain imports

Static-analysis test that walks every module in shared/policies/ and
fails if any import reaches into src.models.orm, shared.table_policies,
or shared.file_policies. This prevents future regressions from re-coupling
the engine to a specific domain."
```

---

## Task 10: Pre-completion verification + PR

**Files:** None.

- [ ] **Step 1: Type-check the whole API**

Run: `cd api && pyright`
Expected: 0 errors.

- [ ] **Step 2: Lint**

Run: `cd api && ruff check .`
Expected: 0 issues.

- [ ] **Step 3: Run the full unit suite**

Run: `./test.sh unit`
Expected: PASS.

- [ ] **Step 4: Run E2E for tables and websocket**

Run: `./test.sh tests/e2e/platform/ -k "table or policy or websocket" -v`
Expected: PASS.

- [ ] **Step 5: Frontend type regeneration (sanity)**

The refactor touches no API endpoints or response models, so types should be unchanged.

Run: `./debug.sh status | grep "Status:   UP" || ./debug.sh up`
Then: `cd client && npm run generate:types`
Expected: `git diff client/src/lib/v1.d.ts` shows no changes.

- [ ] **Step 6: Open PR**

```bash
git push -u origin 170-file-policies
gh pr create --title "refactor(policies): domain-agnostic engine with Resolver/Binding protocols" --body "$(cat <<'EOF'
## Summary

- Extract `Expr`/`Policy`/`PolicyDocument` into `shared/policies/ast.py`
- Introduce `Resolver` and `Binding` protocols for domain-agnostic reference resolution
- Move table-specific code (RowResolver, TableBinding, compile_read_filter, make_seed_admin_bypass) into `shared/table_policies.py`
- All 8 consumers migrated to new import paths and signatures
- Add a static-analysis test that fails if engine modules ever re-import domain code

Tables behavior is bit-for-bit unchanged. This is the precondition for
the file-policies plan (#170) and is independently valuable per the
file-policies design doc §10.2.

Spec: `docs/superpowers/specs/2026-05-01-file-policies-design.md`
Plan: `docs/superpowers/plans/2026-05-19-policy-engine-extraction.md`

## Test plan

- [ ] Backend unit tests green (`./test.sh unit`)
- [ ] Table E2E suite green (`./test.sh tests/e2e/platform/ -k table`)
- [ ] Websocket E2E suite green (`./test.sh tests/e2e/platform/ -k websocket`)
- [ ] Pyright + ruff clean

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Notes on parallelism for agent teams

This plan is mostly **sequential** — each task depends on the previous one's commits being on disk. If you spin up a team:

- **Tasks 1–6** must execute serially (each task's commit is a precondition for the next).
- **Tasks 7 and 8** can run in **parallel** once Task 6 is committed — they touch disjoint files and depend only on Task 6's `shared.table_policies` module.
- **Task 9** depends on Tasks 7+8.
- **Task 10** depends on Task 9.

A reasonable team split: one agent does Tasks 1–6 serially; two agents fan out on Tasks 7 + 8; then re-converge for Tasks 9 + 10.
