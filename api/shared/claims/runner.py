"""Execute a CustomClaim's query against documents in the caller's org.

Fail-closed: any error during table lookup or WHERE compilation logs a
warning and yields no rows. The resolver translates "no rows" into ``[]``
(list claims) or ``None`` (scalar claims), which evaluator/compiler then
treat as "no access" — matching the spec's safety posture.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from src.models.contracts.claims import CustomClaim
from src.models.contracts.policies import Expr
from src.models.orm.tables import Document, Table

logger = logging.getLogger(__name__)

# Top-level Document columns selectable directly (everything else is a JSON path).
_DOCUMENT_COLUMNS = {"id", "table_id", "created_by", "updated_by"}


def run(claim: CustomClaim, user: Any, db: Any) -> list[dict]:
    """Run the claim's query and return rows as ``[{select: value}, ...]``."""
    table_name = claim.query.table
    org_id = getattr(user, "organization_id", None)

    source = db.execute(
        select(Table).where(Table.organization_id == org_id, Table.name == table_name)
    ).scalar_one_or_none()
    if source is None:
        logger.warning(
            "claim %r references unknown table %r in org %s — returning []",
            claim.name, table_name, org_id,
        )
        return []

    stmt = select(Document).where(Document.table_id == source.id)

    where = claim.query.where
    if where is not None:
        try:
            expr = where if isinstance(where, Expr) else Expr(where)  # type: ignore[arg-type]
            stmt = stmt.where(_compile_to_sql(expr, user))
        except Exception as exc:  # noqa: BLE001 — fail-closed by design
            logger.warning(
                "claim %r WHERE failed to compile (%s) — returning []", claim.name, exc
            )
            return []

    select_key = claim.query.select
    rows = db.execute(stmt).scalars().all()
    return [{select_key: _extract(row, select_key)} for row in rows]


def _compile_to_sql(expr: Expr, user: Any):
    # Local import to avoid pulling SQL-compile machinery at module load.
    from shared.policies.compile import compile_to_sql

    return compile_to_sql(expr, user)


def _extract(row: Document, select_key: str) -> Any:
    if select_key in _DOCUMENT_COLUMNS:
        return getattr(row, select_key)
    data = row.data or {}
    cursor: Any = data
    for part in select_key.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
        if cursor is None:
            return None
    return cursor
