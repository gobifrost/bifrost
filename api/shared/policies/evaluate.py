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
    """Pull a known field off the user; UUIDs are stringified for comparison."""
    val = getattr(user, field, None)
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, list):
        # role_ids list of UUIDs -> list of strings
        return [str(v) if isinstance(v, UUID) else v for v in val]
    return val


def _eval_call(node: dict, ctx: Any, user: Any, resolver: Resolver) -> bool:
    target = node["call"]
    args = [_eval_node(a, ctx, user, resolver) for a in node.get("args", [])]
    fn = FUNCTIONS[target]
    # FunctionDef.evaluate is typed `(args, user, dict) -> bool` because the only
    # registered function (has_role) doesn't use the row. When ctx is not a dict
    # (e.g., a DELETE event with no payload, or a non-row domain like files),
    # pass {} — registering a future function that needs row data requires
    # widening FunctionDef.evaluate's third arg to Any.
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
    """Equality with NULL-as-false semantics (matches SQL).

    If either operand resolves to None at evaluate time (e.g. a reference
    to a missing field), returns False. Literal None is rejected at validate
    time, so the only legitimate caller of this guard is a reference
    resolving to None. Use `is_null` to test for null.
    """
    if a is None or b is None:
        return False
    return a == b
