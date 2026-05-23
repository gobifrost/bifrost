"""Pure-function policy evaluator.

Takes an Expr, a row dict, and a user-like object; returns bool.
No DB access. No side effects. Used at REST handler call sites for
per-row decisions and at websocket fanout for per-message filtering.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from shared.policies.functions import FUNCTIONS
from src.models.contracts.policies import Expr


def evaluate(expr: Expr, row: dict | None, user: Any) -> bool:
    """Evaluate an expression against a row + user, return bool.

    `row` may be a dict (the typical case) or None (e.g., DELETE events
    with no payload). Missing keys resolve to None. UUID-typed values
    in user are stringified before comparison.
    """
    return bool(_eval_node(expr.root, row, user))


def _eval_node(node: Any, row: dict | None, user: Any) -> Any:
    """Resolve a node to its value (literal, reference, or operator result)."""
    # Literals
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    if isinstance(node, list):
        return [_eval_node(item, row, user) for item in node]

    # References
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {"row"}:
            return _resolve_row_path(row, node["row"])
        if keys == {"user"}:
            return _resolve_user_field(user, node["user"])
        if keys == {"claims"}:
            return _resolve_claims_field(user, node["claims"])
        if "call" in keys:
            return _eval_call(node, row, user)
        # Operators: single-key dict
        if len(keys) == 1:
            op = next(iter(keys))
            return _eval_op(op, node[op], row, user)

    raise ValueError(f"unevaluatable node: {node!r}")


def _resolve_row_path(row: dict | None, path: str) -> Any:
    """Resolve dot-path against the row dict; missing keys return None."""
    parts = path.split(".")
    cur: Any = row
    for part in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _resolve_user_field(user: Any, field: str) -> Any:
    """Pull a known field off the user; UUIDs are stringified for comparison."""
    val = getattr(user, field, None)
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, list):
        # role_ids list of UUIDs -> list of strings
        return [str(v) if isinstance(v, UUID) else v for v in val]
    return val


def _resolve_claims_field(user: Any, name: str) -> Any:
    """Look up a pre-resolved claim on the principal.

    The evaluator MUST NOT trigger DB I/O — the REST handler / websocket
    fanout pre-resolves every claim referenced in the expression BEFORE
    invoking the evaluator. If the claim is absent here, fail-closed.
    """
    cache = getattr(user, "claims", None) or {}
    if name not in cache:
        return []  # fail-closed
    return cache[name]


def _eval_call(node: dict, row: dict | None, user: Any) -> bool:
    target = node["call"]
    args = [_eval_node(a, row, user) for a in node.get("args", [])]
    fn = FUNCTIONS[target]
    return fn.evaluate(args, user, row or {})


def _eval_op(op: str, value: Any, row: dict | None, user: Any) -> bool:
    if op == "and":
        for item in value:
            if not _eval_node(item, row, user):
                return False
        return True
    if op == "or":
        for item in value:
            if _eval_node(item, row, user):
                return True
        return False
    if op == "not":
        return not _eval_node(value, row, user)
    if op == "eq":
        return _scalar_eq(_eval_node(value[0], row, user), _eval_node(value[1], row, user))
    if op == "neq":
        return not _scalar_eq(_eval_node(value[0], row, user), _eval_node(value[1], row, user))
    if op in ("lt", "lte", "gt", "gte"):
        a = _eval_node(value[0], row, user)
        b = _eval_node(value[1], row, user)
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
        a = _eval_node(value[0], row, user)
        if a is None:
            return False
        b = _eval_node(value[1], row, user)
        if not isinstance(b, list):
            return False
        return a in b
    if op == "is_null":
        return _eval_node(value, row, user) is None
    raise ValueError(f"unknown operator {op!r}")


def _scalar_eq(a: Any, b: Any) -> bool:
    """Equality with NULL-as-false semantics (matches SQL).

    If either operand resolves to None at evaluate time (e.g. a {"row": ...}
    reference to a missing field), returns False. Literal None is rejected at
    validate time, so the only legitimate caller of this guard is a reference
    resolving to None. Use `is_null` to test for null.
    """
    if a is None or b is None:
        return False
    return a == b
