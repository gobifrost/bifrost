"""SQL compiler for policy expressions.

Compiles an Expr to a SQLAlchemy boolean expression suitable for ANDing
into a SELECT against the documents table. User-side facts and function
calls are resolved at compile time; the resulting SQL contains only
parameterized literals against the `documents` table.
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

from shared.policies.functions import FUNCTIONS
from src.models.contracts.policies import Expr
from src.models.orm.tables import Document

# Column-mapped row references — read from the SQL column, not JSONB.
_COLUMN_MAPPED_ROW_FIELDS: dict[str, Any] = {
    "id": Document.id,
    "organization_id": None,  # documents has no organization_id; comes from join — see note below
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


def compile_to_sql(expr: Expr, user: Any) -> ColumnElement:
    """Compile an Expr to a SQLAlchemy boolean expression."""
    return _compile_node(expr.root, user)


def _compile_node(node: Any, user: Any) -> ColumnElement:
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {"user"}:
            return _resolve_user_to_literal(user, node["user"])
        if keys == {"row"}:
            return _resolve_row_to_column(node["row"])
        if "call" in keys:
            return _compile_call(node, user)
        if len(keys) == 1:
            op = next(iter(keys))
            return _compile_op(op, node[op], user)
    if isinstance(node, (str, int, float, bool)) or node is None:
        return literal(node)
    raise ValueError(f"unrendable node: {node!r}")


def _resolve_user_to_literal(user: Any, field: str) -> ColumnElement:
    val = getattr(user, field, None)
    if isinstance(val, UUID):
        val = str(val)
    return literal(val)


def _resolve_row_to_column(path: str) -> ColumnElement:
    parts = path.split(".")
    if len(parts) == 1 and parts[0] in _COLUMN_MAPPED_ROW_FIELDS:
        col = _COLUMN_MAPPED_ROW_FIELDS[parts[0]]
        if col is not None:
            return col
    # JSONB path
    if len(parts) == 1:
        return Document.data[parts[0]].astext
    # Nested: data #>> '{a,b,c}'
    return Document.data[parts].astext  # SQLAlchemy supports list keys


def _compile_call(node: dict, user: Any) -> ColumnElement:
    target = node["call"]
    args = [_resolve_arg_for_call(a, user) for a in node.get("args", [])]
    fn = FUNCTIONS[target]
    result = fn.compile(args, user)
    return sa_true() if result else sa_false()


def _resolve_arg_for_call(arg: Any, user: Any) -> Any:
    """Resolve a call arg to its concrete Python value at compile time.

    Function args must be literals or {"user": ...} references — row
    references can't be resolved at compile time. The validator allows
    {"row": ...} structurally, but reaching it here is a programming error;
    surfacing as ValueError prevents silently-wrong SQL.
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


def _compile_op(op: str, value: Any, user: Any) -> ColumnElement:
    if op == "and":
        return sa_and(*(_compile_node(item, user) for item in value))
    if op == "or":
        return sa_or(*(_compile_node(item, user) for item in value))
    if op == "not":
        # `.self_group()` prevents SQLAlchemy from collapsing
        # `not_(x == y)` into `x != y`; we want a literal NOT(...) so
        # NULL-as-false semantics survive the negation.
        return sa_not(_compile_node(value, user).self_group())
    if op == "eq":
        return _compile_node(value[0], user) == _compile_node(value[1], user)
    if op == "neq":
        return _compile_node(value[0], user) != _compile_node(value[1], user)
    if op == "lt":
        return _compile_node(value[0], user) < _compile_node(value[1], user)
    if op == "lte":
        return _compile_node(value[0], user) <= _compile_node(value[1], user)
    if op == "gt":
        return _compile_node(value[0], user) > _compile_node(value[1], user)
    if op == "gte":
        return _compile_node(value[0], user) >= _compile_node(value[1], user)
    if op == "in":
        left = _compile_node(value[0], user)
        return left.in_(value[1])
    if op == "is_null":
        return _compile_node(value, user).is_(None)
    raise ValueError(f"unknown operator {op!r}")
