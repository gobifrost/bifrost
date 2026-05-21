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
    """Resolve a call arg to its concrete Python value at compile time.

    Function args must be literals or {"user": ...} references — domain
    references (e.g. {"row": ...}, {"file": ...}) can't be resolved at compile
    time because they need a row context. The validator accepts dict args
    structurally; reaching here with anything other than {"user": ...} is a
    programming error.
    """
    if isinstance(arg, dict):
        keys = set(arg.keys())
        if keys == {"user"}:
            return getattr(user, arg["user"], None)
        raise ValueError(
            f"function call args must be literals or {{user: ...}}; "
            f"got {arg!r} which the SQL compiler cannot resolve"
        )
    return arg  # literal


def _compile_op(op: str, value: Any, user: Any, binding: Binding) -> ColumnElement[Any]:
    if op == "and":
        return sa_and(*(_compile_node(item, user, binding) for item in value))
    if op == "or":
        return sa_or(*(_compile_node(item, user, binding) for item in value))
    if op == "not":
        # `.self_group()` prevents SQLAlchemy from collapsing
        # `not_(x == y)` into `x != y`; we want a literal NOT(...) so
        # NULL-as-false semantics survive the negation.
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
