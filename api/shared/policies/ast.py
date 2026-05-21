"""Pydantic AST types for policy expressions. Domain-agnostic.

The AST validator enforces structural correctness. `{user: ...}` references
are validated against `KNOWN_USER_FIELDS`; all other single-key namespaces
(`{row: ...}`, `{file: ...}`, etc.) are accepted structurally as domain
references — the Resolver validates the field name at evaluate time.
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
# `call` is NOT included here — it is intercepted in _validate_operand
# before the operator branch, since {"call": ..., "args": ...} can have
# two keys and doesn't fit the single-key operator shape.
_OTHER_OPS: Final[frozenset[str]] = frozenset({"in", "is_null"})
_ALL_OPS: Final[frozenset[str]] = _LOGIC_OPS | _COMPARE_OPS | _OTHER_OPS

# Known domain-reference namespaces. The validator accepts these as opaque
# `{ <namespace>: "field.path" }` references and trusts the domain's Resolver
# to validate the field name at evaluate time. Adding a new domain (e.g. for
# documents, runs) means adding its namespace here so the AST validator stops
# silently accepting `{"<typo>": ...}` as a domain reference.
_KNOWN_NAMESPACES: Final[frozenset[str]] = frozenset({"row", "file"})

_DEPTH_LIMIT: Final[int] = 64


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
    # `call` is handled here (before the single-key operator branch) because
    # it can carry an `args` key, making it a two-key dict rather than the
    # single-key shape used by every other operator.
    if keys == {"call", "args"} or keys == {"call"}:
        _validate_call(node, depth=depth, path=path)
        return
    if len(keys) == 1:
        op = next(iter(keys))
        if op in _ALL_OPS:
            _validate_op_node(op, node[op], depth=depth, path=path)
            return
        if op in _KNOWN_NAMESPACES:
            # Domain reference: `{ <namespace>: "path.path" }`.
            # Shape validated here; the Resolver validates the field name.
            ref = node[op]
            if not isinstance(ref, str) or not ref:
                raise ValueError(
                    f"{path}: {op!r} reference must be a non-empty string, got {ref!r}"
                )
            return
        raise ValueError(
            f"{path}: unknown operator or namespace {op!r}; "
            f"operators: {sorted(_ALL_OPS)}, namespaces: {sorted(_KNOWN_NAMESPACES)}"
        )
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


class Policy(BaseModel):
    """One rule in a policy document. Domain-agnostic.

    `actions` is `list[str]`. Each domain re-types this via Literal at its
    boundary (e.g. `contracts/policies.py` defines a tables-narrowed Policy
    where `actions` is `list[Literal["read","create","update","delete"]]`).
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
