"""Pydantic types for table policies — the AST validates here."""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

from shared.policies.functions import FUNCTIONS

# Known fields on the USER namespace — the validator rejects anything else.
KNOWN_USER_FIELDS: Final[frozenset[str]] = frozenset({
    "user_id",
    "email",
    "organization_id",
    "is_platform_admin",
    "role_ids",
    "role_names",
})

# Operators that produce boolean results.
_LOGIC_OPS: Final[frozenset[str]] = frozenset({"and", "or", "not"})
_COMPARE_OPS: Final[frozenset[str]] = frozenset({"eq", "neq", "lt", "lte", "gt", "gte"})
_OTHER_OPS: Final[frozenset[str]] = frozenset({"in", "is_null", "call"})
_ALL_OPS: Final[frozenset[str]] = _LOGIC_OPS | _COMPARE_OPS | _OTHER_OPS

# Bound on AST recursion depth. Prevents pathological deeply-nested imports
# (e.g., manifest expressions from an untrusted source) from hitting Python's
# recursion limit and surfacing as an opaque RecursionError → 500.
_DEPTH_LIMIT: Final[int] = 64


def _validate_operand(node: Any, depth: int = 0, path: str = "$") -> None:
    """Recursively validate that a node is a literal, reference, or expression."""
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
    if keys == {"row"}:
        ref = node["row"]
        if not isinstance(ref, str) or not ref:
            raise ValueError(
                f"{path}: row reference must be a non-empty string, got {ref!r}"
            )
        return
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
    # Any other operator dict
    if len(keys) != 1:
        raise ValueError(
            f"{path}: operator node must have exactly one key, got {sorted(keys)}"
        )
    op = next(iter(keys))
    if op not in _ALL_OPS:
        raise ValueError(f"{path}: unknown operator {op!r}")
    _validate_op_node(op, node[op], depth=depth, path=path)


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
            raise ValueError(
                f"{path}.{op}: in requires [operand, [literal, ...]] or "
                f"[operand, {{claims: <name>}}]"
            )
        left, right = value
        _validate_operand(left, depth + 1, f"{path}.{op}[0]")

        # Claims reference RHS: {claims: <name>} — scoped to in-RHS only.
        if isinstance(right, dict) and set(right.keys()) == {"claims"}:
            ref = right["claims"]
            if not isinstance(ref, str) or not ref:
                raise ValueError(
                    f"{path}.{op}[1]: claims reference must be a non-empty string, "
                    f"got {ref!r}"
                )
            return

        if not isinstance(right, list) or not right:
            raise ValueError(
                f"{path}.{op}: in requires a non-empty literal list or "
                f"{{claims: <name>}} as RHS"
            )
        for i, item in enumerate(right):
            if not isinstance(item, (str, int, float, bool)) and item is not None:
                raise ValueError(
                    f"{path}.{op}[1][{i}]: in literal list items must be scalars or null"
                )
        return
    if op == "is_null":
        # Single operand (not a list)
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
        # `arg_types` is the contract for LITERAL args only. Reference args
        # ({"row": "..."}, {"user": "..."}) bypass the type check here because
        # their resolved value is only known at evaluate time. The evaluator
        # is responsible for handling type mismatches at the row.
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


Action = Literal["read", "create", "update", "delete"]


class Policy(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    actions: list[Action] = Field(min_length=1)
    when: Expr | None = None

    @field_validator("actions")
    @classmethod
    def _no_dup_actions(cls, v: list[Action]) -> list[Action]:
        if len(set(v)) != len(v):
            raise ValueError("actions must not contain duplicates")
        return v


class TablePolicies(BaseModel):
    policies: list[Policy] = Field(default_factory=list)


class PolicyValidationError(BaseModel):
    """Single structured validation error for a policy document.

    `path` is a JSONPath-like string pointing into the document (e.g.
    ``$.policies[0].when.eq[1]``); `message` is the validator's prose
    error stripped of any embedded path prefix.
    """

    path: str
    message: str


class PolicyValidationResponse(BaseModel):
    """Outcome of a `POST /api/tables/policies/validate` call.

    `ok` mirrors whether the document validated cleanly. On failure, every
    error from the AST validator is surfaced via `errors`. Endpoint always
    returns 200 — callers parse this body to render structured feedback,
    which is why the validator's `ValueError` is not allowed to escape into
    a FastAPI 422.
    """

    ok: bool
    errors: list[PolicyValidationError] = Field(default_factory=list)
