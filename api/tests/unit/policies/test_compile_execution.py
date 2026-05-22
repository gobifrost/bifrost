"""Postgres-execution tests for the policy SQL compiler.

The unit tests in ``test_compile.py`` only verify rendered SQL strings.
This file actually executes compiled expressions against real Postgres,
which is the only way to catch the bug class where rendered SQL parses
but fails at execution (e.g. the original ``data->>'finalized' = TRUE``
which is text-vs-bool and raises in Postgres).

Spec contract being verified: when ``{eq: [{row: bool_field}, true]}``
is compiled and run, it must:
  - not raise at execution (the original bug)
  - return rows where the JSONB value is the matching bool
  - silently exclude rows where the field is a type mismatch (string,
    missing, etc.) instead of raising
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.policies.compile import compile_to_sql
from src.models.contracts.policies import Expr
from src.models.orm.tables import Document, Table


class _User:
    def __init__(self, user_id=None):
        self.user_id = user_id or uuid4()
        self.organization_id = None
        self.is_platform_admin = False
        self.role_ids: list = []
        self.role_names: list = []


async def _seed_table_with_rows(db_session: AsyncSession, rows: list[dict]) -> Table:
    """Insert a Table and one Document per row payload; return the Table."""
    table = Table(id=uuid4(), name=f"t_{uuid4().hex[:8]}", organization_id=None)
    db_session.add(table)
    await db_session.flush()
    for row in rows:
        db_session.add(Document(id=str(uuid4()), table_id=table.id, data=row))
    await db_session.flush()
    return table


async def _run(db_session: AsyncSession, table: Table, expr_dict: dict) -> set[str]:
    """Compile expr, run against ``documents`` filtered to this table, return matched ids."""
    expr = Expr.model_validate(expr_dict)
    where = compile_to_sql(expr, _User())
    stmt = select(Document.id).where(Document.table_id == table.id).where(where)
    result = await db_session.execute(stmt)
    return {row[0] for row in result.all()}


# --- Bool field ----------------------------------------------------------

@pytest.mark.asyncio
async def test_exec_eq_bool_true_matches_only_bool_true(db_session: AsyncSession):
    """`{eq: [{row: finalized}, true]}` returns ONLY rows where finalized==true.
    Type mismatches (string "yes", missing field) are silently excluded.
    """
    rows = [
        {"finalized": True, "label": "match"},
        {"finalized": False, "label": "bool-false"},
        {"finalized": "yes", "label": "string-mismatch"},
        {"label": "missing-field"},
    ]
    table = await _seed_table_with_rows(db_session, rows)

    matched_ids = await _run(db_session, table, {"eq": [{"row": "finalized"}, True]})

    # We don't know the assigned doc ids, so re-fetch and inspect labels.
    fetched = await db_session.execute(
        select(Document.id, Document.data).where(Document.table_id == table.id)
    )
    by_id = {doc_id: data for doc_id, data in fetched.all()}
    matched_labels = {by_id[doc_id]["label"] for doc_id in matched_ids}
    assert matched_labels == {"match"}, (
        f"expected only the bool-true row; got {matched_labels}"
    )


@pytest.mark.asyncio
async def test_exec_eq_bool_false_matches_only_bool_false(db_session: AsyncSession):
    rows = [
        {"finalized": True, "label": "bool-true"},
        {"finalized": False, "label": "match"},
        {"finalized": "no", "label": "string-mismatch"},
    ]
    table = await _seed_table_with_rows(db_session, rows)

    matched_ids = await _run(db_session, table, {"eq": [{"row": "finalized"}, False]})

    fetched = await db_session.execute(
        select(Document.id, Document.data).where(Document.table_id == table.id)
    )
    by_id = {doc_id: data for doc_id, data in fetched.all()}
    matched_labels = {by_id[doc_id]["label"] for doc_id in matched_ids}
    assert matched_labels == {"match"}


# --- Numeric field --------------------------------------------------------

@pytest.mark.asyncio
async def test_exec_eq_int_matches_only_matching_number(db_session: AsyncSession):
    rows = [
        {"count": 5, "label": "match"},
        {"count": 10, "label": "different-number"},
        {"count": "5", "label": "string-mismatch"},
    ]
    table = await _seed_table_with_rows(db_session, rows)

    matched_ids = await _run(db_session, table, {"eq": [{"row": "count"}, 5]})

    fetched = await db_session.execute(
        select(Document.id, Document.data).where(Document.table_id == table.id)
    )
    by_id = {doc_id: data for doc_id, data in fetched.all()}
    matched_labels = {by_id[doc_id]["label"] for doc_id in matched_ids}
    assert matched_labels == {"match"}


@pytest.mark.asyncio
async def test_exec_lt_int_executes_and_orders_numerics(db_session: AsyncSession):
    """`{lt: [{row: count}, 10]}` must execute (no error) and order numerics.

    The critical property — and the whole point of this fix — is that the
    query EXECUTES against Postgres. The original `data->>'count' < 10`
    (text-vs-int) raised. The JSONB-compare form `data->'count' < '10'::jsonb`
    runs cleanly.

    For type mismatches in row data (e.g. `count = "five"`), JSONB's cross-
    type ordering kicks in — Postgres orders by type tag, not the underlying
    value. This isn't "silent false" the way `eq` is, but it never raises,
    which is what matters for security policy enforcement. The pure-Python
    evaluator does return silent-false on `lt` type mismatches (see
    evaluate.py:_eval_op), but the compiler matches Postgres semantics here
    rather than introducing per-row `jsonb_typeof` guards on the hot path.
    """
    rows = [
        {"count": 5, "label": "below"},
        {"count": 15, "label": "above"},
    ]
    table = await _seed_table_with_rows(db_session, rows)

    matched_ids = await _run(db_session, table, {"lt": [{"row": "count"}, 10]})

    fetched = await db_session.execute(
        select(Document.id, Document.data).where(Document.table_id == table.id)
    )
    by_id = {doc_id: data for doc_id, data in fetched.all()}
    matched_labels = {by_id[doc_id]["label"] for doc_id in matched_ids}
    assert matched_labels == {"below"}


# --- Real-world shape: shared_read_in_org ---------------------------------

@pytest.mark.asyncio
async def test_exec_shared_read_in_org_shape(db_session: AsyncSession):
    """The exact policy shape that motivated this fix:
    `{eq: [{row: shared}, true]}` for collaborative artifact sharing.
    """
    rows = [
        {"shared": True, "title": "shared-doc"},
        {"shared": False, "title": "private-doc"},
        {"title": "missing-flag"},
    ]
    table = await _seed_table_with_rows(db_session, rows)

    matched_ids = await _run(db_session, table, {"eq": [{"row": "shared"}, True]})

    fetched = await db_session.execute(
        select(Document.id, Document.data).where(Document.table_id == table.id)
    )
    by_id = {doc_id: data for doc_id, data in fetched.all()}
    matched_titles = {by_id[doc_id]["title"] for doc_id in matched_ids}
    assert matched_titles == {"shared-doc"}
