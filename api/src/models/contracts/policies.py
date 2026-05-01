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


def _validate_operand(node: Any) -> None:
    """Recursively validate that a node is a literal, reference, or expression."""
    if isinstance(node, (str, int, float, bool)) or node is None:
        return
    if isinstance(node, list):
        for item in node:
            _validate_operand(item)
        return
    if not isinstance(node, dict):
        raise ValueError(f"unexpected operand type: {type(node).__name__}")

    keys = set(node.keys())
    if keys == {"row"}:
        ref = node["row"]
        if not isinstance(ref, str) or not ref:
            raise ValueError(f"row reference must be a non-empty string, got {ref!r}")
        return
    if keys == {"user"}:
        ref = node["user"]
        if ref not in KNOWN_USER_FIELDS:
            raise ValueError(
                f"unknown user field {ref!r}; available: {sorted(KNOWN_USER_FIELDS)}"
            )
        return
    if keys == {"call", "args"} or keys == {"call"}:
        _validate_call(node)
        return
    # Any other operator dict
    if len(keys) != 1:
        raise ValueError(f"operator node must have exactly one key, got {sorted(keys)}")
    op = next(iter(keys))
    if op not in _ALL_OPS:
        raise ValueError(f"unknown operator {op!r}")
    _validate_op_node(op, node[op])


def _validate_op_node(op: str, value: Any) -> None:
    if op in _LOGIC_OPS - {"not"}:
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError(f"{op} requires at least two operands")
        for item in value:
            _validate_operand(item)
        return
    if op == "not":
        if isinstance(value, list):
            raise ValueError("not requires exactly one operand (not a list)")
        _validate_operand(value)
        return
    if op in _COMPARE_OPS:
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"{op} requires exactly two operands")
        if op in {"eq", "neq"}:
            for operand in value:
                if operand is None:
                    raise ValueError(
                        f"{op} does not accept null literals (NULL semantics differ "
                        "between evaluator and SQL pushdown); use is_null instead"
                    )
        for item in value:
            _validate_operand(item)
        return
    if op == "in":
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("in requires [operand, [literal, ...]]")
        left, right = value
        _validate_operand(left)
        if not isinstance(right, list) or not right:
            raise ValueError("in requires a non-empty literal list as second arg")
        for item in right:
            if not isinstance(item, (str, int, float, bool)) and item is not None:
                raise ValueError("in literal list items must be scalars or null")
        return
    if op == "is_null":
        # Single operand (not a list)
        if isinstance(value, list):
            raise ValueError("is_null requires exactly one operand (not a list)")
        _validate_operand(value)
        return


def _validate_call(node: dict) -> None:
    target = node.get("call")
    args = node.get("args", [])
    if not isinstance(target, str):
        raise ValueError("call target must be a string")
    if target not in FUNCTIONS:
        raise ValueError(
            f"unknown function {target!r}; available: {sorted(FUNCTIONS)}"
        )
    fn = FUNCTIONS[target]
    if len(args) != len(fn.arg_types):
        raise ValueError(
            f"function {target!r} expects {len(fn.arg_types)} args, got {len(args)}"
        )
    for i, (arg, t) in enumerate(zip(args, fn.arg_types)):
        # `arg_types` is the contract for LITERAL args only. Reference args
        # ({"row": "..."}, {"user": "..."}) bypass the type check here because
        # their resolved value is only known at evaluate time. The evaluator
        # is responsible for handling type mismatches at the row.
        if isinstance(arg, dict):
            _validate_operand(arg)
            continue
        if not isinstance(arg, t):
            raise ValueError(
                f"function {target!r} arg {i} expected {t.__name__}, "
                f"got {type(arg).__name__}"
            )


class Expr(RootModel[dict]):
    """Policy expression AST. Validated at construction."""

    @model_validator(mode="after")
    def _validate(self):
        _validate_operand(self.root)
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
