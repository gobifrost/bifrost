"""Table-domain bindings for the shared policy engine.

This module is the only thing that knows about `Document`, the
`_COLUMN_MAPPED_ROW_FIELDS` mapping, the table action vocab (`read`,
`create`, `update`, `delete`), and the seeded admin-bypass shape for
tables. The shared engine in `shared/policies/` consumes these via the
Resolver and Binding protocols.
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
