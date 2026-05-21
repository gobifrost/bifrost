"""SQL compiler for policy expressions.

Compiles an Expr to a SQLAlchemy boolean expression suitable for ANDing
into a SELECT against the documents table. User-side facts and function
calls are resolved at compile time; the resulting SQL contains only
parameterized literals against the `documents` table.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import json

from sqlalchemy import and_ as sa_and
from sqlalchemy import cast as sa_cast
from sqlalchemy import false as sa_false
from sqlalchemy import literal
from sqlalchemy import not_ as sa_not
from sqlalchemy import or_ as sa_or
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
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


def _resolve_row_to_jsonb(path: str) -> ColumnElement | None:
    """Return the JSONB-form extract (data->'field') for a row path, or None
    if the path resolves to a column-mapped field (which is not JSONB).

    Used for comparisons against non-string Python literals (bool, int, float),
    where the text-form `data->>'field' = TRUE` produces invalid Postgres SQL.
    The JSONB compare form `data->'field' = 'true'::jsonb` is type-aware and
    silently returns false on type mismatch instead of raising.
    """
    parts = path.split(".")
    if len(parts) == 1 and parts[0] in _COLUMN_MAPPED_ROW_FIELDS:
        col = _COLUMN_MAPPED_ROW_FIELDS[parts[0]]
        if col is not None:
            return None  # column-mapped — caller falls back to text path
    if len(parts) == 1:
        return Document.data[parts[0]]
    return Document.data[parts]


def _jsonb_literal(value: Any) -> ColumnElement:
    """Wrap a Python value as a JSONB literal: `'{json}'::jsonb`."""
    return sa_cast(literal(json.dumps(value)), JSONB)


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


def _resolve_to_scalar(node: Any, user: Any) -> Any:
    """If `node` resolves to a concrete Python scalar at compile time, return it.

    Returns the scalar for: bare literals, `{user: field}` references whose
    resolved value is a non-UUID scalar, and `{call: ...}` results (already
    a Python bool from `has_role`). Returns a sentinel `_NOT_SCALAR` otherwise
    (notably for `{row: ...}` references, which only resolve at SQL execution).
    """
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    if isinstance(node, dict):
        keys = set(node.keys())
        if keys == {"user"}:
            val = getattr(user, node["user"], None)
            if isinstance(val, UUID):
                return str(val)
            return val
    return _NOT_SCALAR


_NOT_SCALAR = object()


def _compile_comparison_operands(
    left_node: Any, right_node: Any, user: Any
) -> tuple[ColumnElement, ColumnElement]:
    """Compile both sides of a comparison, switching to JSONB-compare form
    when one side is a JSONB-backed row reference and the other resolves to
    a non-string Python scalar (bool / int / float).

    The default text-form (`data->>'field' = '<value>'`) only works when the
    right side is a string. For bool/int/float, `data->>'field' = TRUE` is
    invalid Postgres SQL. JSONB-compare (`data->'field' = '<json>'::jsonb`)
    is type-aware: type mismatches in row data return false instead of
    raising. Spec: docs/superpowers/specs/2026-04-30-table-policies-design.md
    """
    left_row = (
        left_node["row"]
        if isinstance(left_node, dict) and set(left_node.keys()) == {"row"}
        else None
    )
    right_row = (
        right_node["row"]
        if isinstance(right_node, dict) and set(right_node.keys()) == {"row"}
        else None
    )

    # Detect the "JSONB row vs non-string scalar literal" shape on either side.
    if left_row is not None and right_row is None:
        scalar = _resolve_to_scalar(right_node, user)
        if scalar is not _NOT_SCALAR and isinstance(scalar, (bool, int, float)) and not isinstance(scalar, str):
            jsonb_col = _resolve_row_to_jsonb(left_row)
            if jsonb_col is not None:
                return jsonb_col, _jsonb_literal(scalar)
    if right_row is not None and left_row is None:
        scalar = _resolve_to_scalar(left_node, user)
        if scalar is not _NOT_SCALAR and isinstance(scalar, (bool, int, float)) and not isinstance(scalar, str):
            jsonb_col = _resolve_row_to_jsonb(right_row)
            if jsonb_col is not None:
                return _jsonb_literal(scalar), jsonb_col

    return _compile_node(left_node, user), _compile_node(right_node, user)


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
    if op in ("eq", "neq", "lt", "lte", "gt", "gte"):
        left, right = _compile_comparison_operands(value[0], value[1], user)
        if op == "eq":
            return left == right
        if op == "neq":
            return left != right
        if op == "lt":
            return left < right
        if op == "lte":
            return left <= right
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
    if op == "in":
        left = _compile_node(value[0], user)
        return left.in_(value[1])
    if op == "is_null":
        return _compile_node(value, user).is_(None)
    raise ValueError(f"unknown operator {op!r}")
