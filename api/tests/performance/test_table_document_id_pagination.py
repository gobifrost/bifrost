"""Query-plan regressions for physical document-ID filtering."""

from __future__ import annotations

from collections.abc import Iterable
import re
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.tables import DocumentQuery
from src.models.orm.organizations import Organization
from src.models.orm.tables import Document, Table
from shared.table_documents import DocumentRepository


def _plan_nodes(plan: dict):
    yield plan
    for child in plan.get("Plans", []):
        yield from _plan_nodes(child)


def _canonical_locale(value: str) -> str:
    return value.lower().replace("-", "")


def _node_types(plan: dict) -> set[str]:
    return {node["Node Type"] for node in _plan_nodes(plan)}


def _index_scan_nodes(plan: dict, index_name: str) -> list[dict]:
    return [
        node
        for node in _plan_nodes(plan)
        if node["Node Type"] in {"Index Scan", "Index Only Scan", "Bitmap Index Scan"}
        and node.get("Index Name") == index_name
    ]


def _child_nodes(plan: dict) -> list[dict]:
    children = []
    for node in _plan_nodes(plan):
        children.extend(node.get("Plans", []))
    return children


def _shared_blocks(plan: dict) -> int:
    # PostgreSQL reports cumulative buffer usage on every plan node. The root
    # therefore already represents the whole statement; summing the tree
    # counts the same buffers again at each ancestor.
    return int(plan.get("Shared Hit Blocks", 0)) + int(
        plan.get("Shared Read Blocks", 0)
    )


def _assert_prefix_index_plan(
    plan: dict,
    *,
    prefix: str,
    like_lower: str | None = None,
    max_shared_blocks: int = 3_000,
    max_index_rows: int = 25_000,
) -> None:
    node_types = _node_types(plan)
    index_nodes = _index_scan_nodes(plan, "ix_documents_table_id_id_c")

    assert "Seq Scan" not in node_types
    assert "Gather Merge" not in node_types
    assert index_nodes
    assert _shared_blocks(plan) <= max_shared_blocks

    index_conditions = "\n".join(
        node.get("Index Cond", "") for node in index_nodes
    )
    rendered_prefix = prefix.replace("\\", "\\\\").replace("%", "%%")
    rendered_like_lower = (like_lower or prefix).replace("\\", "\\\\")
    assert f"(id)::text >= '{rendered_prefix}'::text" in index_conditions
    assert f"(id)::text >= '{rendered_like_lower}'::text" in index_conditions
    assert re.search(r"\(id\)::text < '[^']+'::text", index_conditions)
    assert all(node["Actual Rows"] <= max_index_rows for node in index_nodes)

    if "Sort" in node_types:
        assert any(node["Node Type"] == "Bitmap Heap Scan" for node in _child_nodes(plan))
        assert any(node["Node Type"] == "Bitmap Index Scan" for node in index_nodes)


def _assert_legacy_plan(plan: dict, *, max_shared_blocks: int = 5_000) -> None:
    node_types = _node_types(plan)

    assert "Seq Scan" not in node_types
    assert "Gather Merge" not in node_types
    assert _shared_blocks(plan) <= max_shared_blocks


async def _explain_analyze_json(
    db_session: AsyncSession,
    sql: str,
    params: dict[str, object],
) -> dict:
    await db_session.execute(text("SET LOCAL statement_timeout = '2500ms'"))
    result = await db_session.execute(
        text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"),
        params,
    )
    return result.scalar_one()[0]


async def _explain_repository_prefix_query(
    db_session: AsyncSession,
    table: Table,
    query: DocumentQuery,
) -> tuple[list[str], dict]:
    captured = []

    def capture_statement(orm_execute_state):
        if orm_execute_state.is_select:
            captured.append(orm_execute_state.statement)

    event.listen(db_session.sync_session, "do_orm_execute", capture_statement)
    try:
        documents, total = await DocumentRepository(db_session, table).query(query)
    finally:
        event.remove(db_session.sync_session, "do_orm_execute", capture_statement)

    assert total == -1
    assert len(captured) == 1
    sql = str(
        captured[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    explained = await _explain_analyze_json(db_session, sql, {})
    return [document.id for document in documents], explained


async def _explain_legacy_prefix_query(
    db_session: AsyncSession,
    table: Table,
    *,
    prefix: str,
    after_document_id: str | None,
    limit: int,
) -> dict:
    cursor_predicate = (
        ""
        if after_document_id is None
        else f"AND id > '{after_document_id}'"
    )
    sql = f"""
        SELECT table_id, id, data, created_at, updated_at, created_by, updated_by
        FROM documents
        WHERE table_id = '{table.id}'::uuid
            AND id >= '{prefix}'
            AND id LIKE '{prefix}%%' ESCAPE '/'
            {cursor_predicate}
        ORDER BY id
        LIMIT {limit} OFFSET 0
    """
    return await _explain_analyze_json(db_session, sql, {})


async def _fetch_page(
    db_session: AsyncSession,
    table: Table,
    *,
    prefix: str,
    after_document_id: str | None = None,
    limit: int = 500,
) -> list[str]:
    documents, total = await DocumentRepository(db_session, table).query(
        DocumentQuery(
            document_id_prefix=prefix,
            after_document_id=after_document_id,
            skip_count=True,
            limit=limit,
        )
    )

    assert total == -1
    return [document.id for document in documents]


async def _walk_prefix(
    db_session: AsyncSession,
    table: Table,
    *,
    prefix: str,
    limit: int,
) -> list[str]:
    document_ids: list[str] = []
    cursor: str | None = None

    while True:
        page = await _fetch_page(
            db_session,
            table,
            prefix=prefix,
            after_document_id=cursor,
            limit=limit,
        )
        if not page:
            return document_ids

        assert not set(document_ids).intersection(page)
        document_ids.extend(page)
        cursor = page[-1]


async def _assert_complete_prefix(
    db_session: AsyncSession,
    table: Table,
    *,
    prefix: str,
    expected_ids: Iterable[str],
    limit: int,
) -> None:
    expected = list(expected_ids)
    actual = await _walk_prefix(db_session, table, prefix=prefix, limit=limit)

    assert actual == expected
    assert len(actual) == len(set(actual))


@pytest.mark.asyncio
async def test_performance_database_uses_en_us_utf8_locale(
    db_session: AsyncSession,
) -> None:
    """Document prefix evidence is captured against the production locale."""
    result = await db_session.execute(
        text(
            """
            SELECT
                current_setting('server_version') AS server_version,
                datcollate AS lc_collate,
                datctype AS lc_ctype
            FROM pg_database
            WHERE datname = current_database()
            """,
        )
    )

    row = result.mappings().one()

    assert _canonical_locale(row["lc_collate"]) == "en_us.utf8"
    assert _canonical_locale(row["lc_ctype"]) == "en_us.utf8"


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(1800)
async def test_document_id_prefix_pages_stay_index_bounded_at_realistic_scale(
    db_session: AsyncSession,
) -> None:
    """Prefix keyset plans stay bounded with millions of unrelated documents."""
    target_prefix = "11111111-1111-4111-8111-111111111111|drive|"
    small_prefix = "22222222-2222-4222-8222-222222222222|drive|"
    missing_prefix = "33333333-3333-4333-8333-333333333333|drive|"
    org = Organization(
        id=uuid4(),
        name=f"Document prefix scale {uuid4().hex[:8]}",
        domain=f"document-prefix-scale-{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
    )
    large_table = Table(
        id=uuid4(),
        name=f"document_prefix_large_{uuid4().hex[:8]}",
        organization_id=org.id,
        created_by="test@example.com",
    )
    small_table = Table(
        id=uuid4(),
        name=f"document_prefix_small_{uuid4().hex[:8]}",
        organization_id=org.id,
        created_by="test@example.com",
    )
    unrelated_tables = [
        Table(
            id=uuid4(),
            name=f"document_prefix_noise_{idx}_{uuid4().hex[:8]}",
            organization_id=org.id,
            created_by="test@example.com",
        )
        for idx in range(8)
    ]
    db_session.add_all([org, large_table, small_table, *unrelated_tables])
    await db_session.flush()

    unrelated_table_ids = [str(table.id) for table in unrelated_tables]
    await db_session.execute(
        text(
            """
            INSERT INTO documents (table_id, id, data, created_by, updated_by)
            SELECT
                CAST(:table_id AS uuid),
                lpad(to_hex(item % 1048576), 8, '0')
                    || '-aaaa-4aaa-8aaa-'
                    || lpad(to_hex(item % 281474976710655), 12, '0')
                    || '|noise-same-table-'
                    || lpad((item % 64)::text, 2, '0')
                    || '|item-' || lpad(item::text, 7, '0'),
                jsonb_build_object('kind', 'same-table-noise', 'item', item),
                'test@example.com',
                'test@example.com'
            FROM generate_series(1, 1600000) AS item
            """
        ),
        {"table_id": str(large_table.id)},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO documents (table_id, id, data, created_by, updated_by)
            SELECT
                CAST(table_ids[(item % array_length(table_ids, 1)) + 1] AS uuid),
                lpad(to_hex(item % 1048576), 8, '0')
                    || '-bbbb-4bbb-8bbb-'
                    || lpad(to_hex(item % 281474976710655), 12, '0')
                    || '|noise-other-table-'
                    || lpad((item % 8)::text, 2, '0')
                    || '|item-' || lpad(item::text, 7, '0'),
                jsonb_build_object('kind', 'other-table-noise', 'item', item),
                'test@example.com',
                'test@example.com'
            FROM generate_series(1, 400000) AS item
            CROSS JOIN (SELECT CAST(:table_ids AS text[]) AS table_ids) AS ids
            """
        ),
        {"table_ids": unrelated_table_ids},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO documents (table_id, id, data, created_by, updated_by)
            SELECT
                CAST(:table_id AS uuid),
                :target_prefix || 'item-' || lpad(item::text, 5, '0'),
                jsonb_build_object('kind', 'large', 'item', item),
                'test@example.com',
                'test@example.com'
            FROM generate_series(0, 19999) AS item
            """
        ),
        {"table_id": str(large_table.id), "target_prefix": target_prefix},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO documents (table_id, id, data, created_by, updated_by)
            SELECT
                CAST(:table_id AS uuid),
                :small_prefix || 'item-' || lpad(item::text, 2, '0'),
                jsonb_build_object('kind', 'small', 'item', item),
                'test@example.com',
                'test@example.com'
            FROM generate_series(0, 49) AS item
            """
        ),
        {"table_id": str(small_table.id), "small_prefix": small_prefix},
    )
    await db_session.execute(
        insert(Document),
        [
            {
                "table_id": large_table.id,
                "id": f"tenant%_A/folder\\caf\u00e9/{item:02d}",
                "data": {"kind": "escaped", "item": item},
                "created_by": "test@example.com",
                "updated_by": "test@example.com",
            }
            for item in range(12)
        ]
        + [
            {
                "table_id": large_table.id,
                "id": f"tenant%_A/folder\\cafe\u0301/{item:02d}",
                "data": {"kind": "escaped-decomposed", "item": item},
                "created_by": "test@example.com",
                "updated_by": "test@example.com",
            }
            for item in range(12)
        ],
    )
    await db_session.commit()
    await db_session.execute(text("ANALYZE documents"))

    await _assert_complete_prefix(
        db_session,
        large_table,
        prefix=target_prefix,
        expected_ids=(
            f"{target_prefix}item-{item:05d}" for item in range(20_000)
        ),
        limit=777,
    )
    await _assert_complete_prefix(
        db_session,
        small_table,
        prefix=small_prefix,
        expected_ids=(
            f"{small_prefix}item-{item:02d}" for item in range(50)
        ),
        limit=17,
    )
    await _assert_complete_prefix(
        db_session,
        large_table,
        prefix="tenant%_A/folder\\caf\u00e9/",
        expected_ids=(f"tenant%_A/folder\\caf\u00e9/{item:02d}" for item in range(12)),
        limit=5,
    )
    await _assert_complete_prefix(
        db_session,
        large_table,
        prefix="tenant%_A/folder\\cafe\u0301/",
        expected_ids=(
            f"tenant%_A/folder\\cafe\u0301/{item:02d}" for item in range(12)
        ),
        limit=5,
    )
    assert await _fetch_page(
        db_session,
        large_table,
        prefix=target_prefix,
        after_document_id=f"{target_prefix}item-19999",
        limit=500,
    ) == []
    assert await _fetch_page(
        db_session,
        large_table,
        prefix=missing_prefix,
        limit=500,
    ) == []

    plan_cases = [
        ("first", None, target_prefix, None, 500, 500),
        (
            "middle",
            f"{target_prefix}item-09999",
            target_prefix,
            None,
            500,
            500,
        ),
        (
            "deep",
            f"{target_prefix}item-19499",
            target_prefix,
            None,
            500,
            500,
        ),
        (
            "final-empty",
            f"{target_prefix}item-19999",
            target_prefix,
            None,
            500,
            0,
        ),
        ("nonexistent", None, missing_prefix, None, 500, 0),
        (
            "escaped-composed",
            None,
            "tenant%_A/folder\\caf\u00e9/",
            "tenant%",
            20,
            12,
        ),
        (
            "escaped-decomposed",
            None,
            "tenant%_A/folder\\cafe\u0301/",
            "tenant%",
            20,
            12,
        ),
    ]
    for _name, cursor, prefix, like_lower, limit, expected_count in plan_cases:
        document_ids, explained = await _explain_repository_prefix_query(
            db_session,
            large_table,
            DocumentQuery(
                document_id_prefix=prefix,
                after_document_id=cursor,
                skip_count=True,
                limit=limit,
            ),
        )
        assert len(document_ids) == expected_count
        _assert_prefix_index_plan(
            explained["Plan"],
            prefix=prefix,
            like_lower=like_lower,
        )

    legacy_first = await _explain_legacy_prefix_query(
        db_session,
        large_table,
        prefix=target_prefix,
        after_document_id=None,
        limit=500,
    )
    legacy_deep = await _explain_legacy_prefix_query(
        db_session,
        large_table,
        prefix=target_prefix,
        after_document_id=f"{target_prefix}item-19499",
        limit=500,
    )
    legacy_final = await _explain_legacy_prefix_query(
        db_session,
        large_table,
        prefix=target_prefix,
        after_document_id=f"{target_prefix}item-19999",
        limit=500,
    )
    for legacy_plan in (legacy_first, legacy_deep, legacy_final):
        _assert_legacy_plan(legacy_plan["Plan"])

    small_document_ids = await _fetch_page(
        db_session,
        small_table,
        prefix=small_prefix,
        limit=500,
    )
    assert len(small_document_ids) == 50


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(300)
async def test_document_id_prefix_generic_plan_keeps_literal_prefix_bounds(
    db_session: AsyncSession,
) -> None:
    """A generic plan can parameterize table/cursor/limit and still range-seek."""
    org = Organization(
        id=uuid4(),
        name=f"Document prefix generic {uuid4().hex[:8]}",
        domain=f"document-prefix-generic-{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
    )
    table = Table(
        id=uuid4(),
        name=f"document_prefix_generic_{uuid4().hex[:8]}",
        organization_id=org.id,
        created_by="test@example.com",
    )
    db_session.add_all([org, table])
    await db_session.flush()
    await db_session.execute(
        text(
            """
            INSERT INTO documents (table_id, id, data, created_by, updated_by)
            SELECT
                CAST(:table_id AS uuid),
                'tenant-03|drive|item-' || lpad(item::text, 5, '0'),
                jsonb_build_object('item', item),
                'test@example.com',
                'test@example.com'
            FROM generate_series(0, 999) AS item
            """
        ),
        {"table_id": str(table.id)},
    )
    await db_session.commit()
    await db_session.execute(text("ANALYZE documents"))

    await db_session.execute(text("SET LOCAL statement_timeout = '2500ms'"))
    await db_session.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
    await db_session.execute(
        text(
            """
            PREPARE document_prefix_generic_page(uuid, text, integer) AS
            SELECT table_id, id, data, created_at, updated_at, created_by, updated_by
            FROM documents
            WHERE table_id = $1
                AND id COLLATE "C" >= 'tenant-03|drive|'
                AND id COLLATE "C" LIKE 'tenant-03|drive|%' ESCAPE '/'
                AND id COLLATE "C" > $2
            ORDER BY id COLLATE "C"
            LIMIT $3
            """
        )
    )

    first_plan = (
        await db_session.execute(
            text(
                f"""
                EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                EXECUTE document_prefix_generic_page(
                    '{table.id}'::uuid,
                    'tenant-03|drive|',
                    50
                )
                """
            )
        )
    ).scalar_one()[0]
    final_plan = (
        await db_session.execute(
            text(
                f"""
                EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                EXECUTE document_prefix_generic_page(
                    '{table.id}'::uuid,
                    'tenant-03|drive|item-00999',
                    50
                )
                """
            )
        )
    ).scalar_one()[0]

    for explained in (first_plan, final_plan):
        plan = explained["Plan"]
        _assert_prefix_index_plan(plan, prefix="tenant-03|drive|")
        index_conditions = "\n".join(
            node.get("Index Cond", "") for node in _plan_nodes(plan)
        )
        assert re.search(r"\(id\)::text >= 'tenant-03\|drive\|'::text", index_conditions)
        assert re.search(r"\(id\)::text < 'tenant-03\|drive}'::text", index_conditions)
        assert "table_id = $1" in index_conditions
        assert re.search(r"\(id\)::text > \$2", index_conditions)

    await db_session.execute(text("DEALLOCATE document_prefix_generic_page"))


@pytest.mark.asyncio
async def test_document_prefix_statement_uses_c_collation_and_literal_pattern(
    db_session: AsyncSession,
) -> None:
    """The prefix keyset query must match the C-collated expression index."""
    org = Organization(
        id=uuid4(),
        name=f"Document prefix SQL {uuid4().hex[:8]}",
        domain=f"document-prefix-sql-{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
    )
    table = Table(
        id=uuid4(),
        name=f"document_prefix_sql_{uuid4().hex[:8]}",
        organization_id=org.id,
        created_by="test@example.com",
    )
    db_session.add_all([org, table])
    await db_session.flush()

    captured = []

    def capture_statement(orm_execute_state):
        if orm_execute_state.is_select:
            captured.append(orm_execute_state.statement)

    event.listen(db_session.sync_session, "do_orm_execute", capture_statement)
    try:
        await DocumentRepository(db_session, table).query(
            DocumentQuery(
                document_id_prefix="tenant%_A/folder\\caf\u00e9/",
                after_document_id="tenant%_A/folder\\caf\u00e9/001",
                document_ids=[
                    "tenant%_A/folder\\caf\u00e9/001",
                    "tenant%_A/folder\\caf\u00e9/002",
                    "tenant%_A/folder\\caf\u00e9/missing",
                ],
                where={"tenant": "target", "rank": {"gte": 10}},
                skip_count=True,
                limit=25,
            ),
            extra_where=Document.data["policy"].astext == "allow",
        )
    finally:
        event.remove(db_session.sync_session, "do_orm_execute", capture_statement)

    assert len(captured) == 1
    compiled = captured[0].compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    sql = str(compiled)

    assert re.search(
        r"documents\.table_id = %\(table_id_\d+\)s",
        sql,
    )
    assert re.search(
        r'documents\.id COLLATE "C" >= %\([^)]+\)s',
        sql,
    )
    assert re.search(
        r'documents\.id COLLATE "C" > %\([^)]+\)s',
        sql,
    )
    assert (
        "documents.id COLLATE \"C\" LIKE "
        "'tenant/%%/_A//folder\\\\caf\u00e9//%%' ESCAPE '/'"
    ) in sql
    assert 'ORDER BY documents.id COLLATE "C"' in sql

    assert any(key.startswith("table_id_") for key in compiled.params)
    assert table.id in compiled.params.values()
    assert "tenant%_A/folder\\caf\u00e9/001" in compiled.params.values()
    assert "tenant%_A/folder\\caf\u00e9/002" in compiled.params.values()
    assert "tenant%_A/folder\\caf\u00e9/missing" in compiled.params.values()
    assert "target" in compiled.params.values()
    assert "10" in compiled.params.values()
    assert "allow" in compiled.params.values()
    assert all(
        value != "tenant/%/_A//folder\\caf\u00e9//%"
        for value in compiled.params.values()
    )
    assert "IN (" in sql
    assert "documents.data" in sql


@pytest.mark.asyncio
async def test_document_cursor_only_statement_keeps_default_id_order(
    db_session: AsyncSession,
) -> None:
    """Cursor-only document ID pagination preserves the legacy id expression."""
    org = Organization(
        id=uuid4(),
        name=f"Document cursor SQL {uuid4().hex[:8]}",
        domain=f"document-cursor-sql-{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
    )
    table = Table(
        id=uuid4(),
        name=f"document_cursor_sql_{uuid4().hex[:8]}",
        organization_id=org.id,
        created_by="test@example.com",
    )
    db_session.add_all([org, table])
    await db_session.flush()

    captured = []

    def capture_statement(orm_execute_state):
        if orm_execute_state.is_select:
            captured.append(orm_execute_state.statement)

    event.listen(db_session.sync_session, "do_orm_execute", capture_statement)
    try:
        await DocumentRepository(db_session, table).query(
            DocumentQuery(
                after_document_id="tenant-a|001",
                skip_count=True,
                limit=25,
            )
        )
    finally:
        event.remove(db_session.sync_session, "do_orm_execute", capture_statement)

    assert len(captured) == 1
    compiled = captured[0].compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    sql = str(compiled)

    assert "COLLATE" not in sql
    assert re.search(r"documents\.id > %\([^)]+\)s", sql)
    assert "ORDER BY documents.id" in sql
    assert "tenant-a|001" in compiled.params.values()


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(120)
async def test_document_id_keyset_query_can_use_ordered_c_collated_index_scan(
    db_session: AsyncSession,
) -> None:
    """A production-shaped tenant page seeks through the C-collated index."""
    org = Organization(
        id=uuid4(),
        name=f"Document ID plan {uuid4().hex[:8]}",
        domain=f"document-id-plan-{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
    )
    table = Table(
        id=uuid4(),
        name=f"document_id_plan_{uuid4().hex[:8]}",
        organization_id=org.id,
        created_by="test@example.com",
    )
    db_session.add_all([org, table])
    await db_session.flush()

    rows = []
    for tenant in range(5):
        prefix = f"tenant-{tenant:02d}|"
        rows.extend(
            {
                "table_id": table.id,
                "id": f"{prefix}drive|item-{item:05d}",
                "data": {"tenant": tenant, "item": item},
                "created_by": "test@example.com",
                "updated_by": "test@example.com",
            }
            for item in range(4_000)
        )
    await db_session.execute(insert(Document), rows)
    await db_session.commit()
    await db_session.execute(text("ANALYZE documents"))

    captured = []

    def capture_statement(orm_execute_state):
        if orm_execute_state.is_select:
            captured.append(orm_execute_state.statement)

    event.listen(db_session.sync_session, "do_orm_execute", capture_statement)
    try:
        documents, total = await DocumentRepository(db_session, table).query(
            DocumentQuery(
                document_id_prefix="tenant-03|",
                after_document_id="tenant-03|drive|item-01999",
                skip_count=True,
                limit=500,
            )
        )
    finally:
        event.remove(db_session.sync_session, "do_orm_execute", capture_statement)

    assert total == -1
    assert len(documents) == 500
    statement = captured[-1]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    result = await db_session.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"))
    plan = result.scalar_one()[0]["Plan"]
    nodes = list(_plan_nodes(plan))

    assert all(node["Node Type"] != "Sort" for node in nodes)
    assert any(
        node["Node Type"] in {"Index Scan", "Index Only Scan"}
        and node.get("Index Name") == "ix_documents_table_id_id_c"
        for node in nodes
    )


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.timeout(120)
async def test_document_id_batch_query_uses_composite_index(
    db_session: AsyncSession,
) -> None:
    """A large exact-ID batch is resolved through ``documents_pkey``."""
    org = Organization(
        id=uuid4(),
        name=f"Document ID batch plan {uuid4().hex[:8]}",
        domain=f"document-id-batch-plan-{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
    )
    table = Table(
        id=uuid4(),
        name=f"document_id_batch_plan_{uuid4().hex[:8]}",
        organization_id=org.id,
        created_by="test@example.com",
    )
    db_session.add_all([org, table])
    await db_session.flush()

    await db_session.execute(
        text(
            """
            INSERT INTO documents (
                table_id, id, data, created_by, updated_by
            )
            SELECT
                CAST(:table_id AS uuid),
                'tenant|drive|item-' || lpad(item::text, 6, '0'),
                jsonb_build_object('tenant_id', 'tenant', 'item', item),
                'test@example.com',
                'test@example.com'
            FROM generate_series(0, 199999) AS item
            """
        ),
        {"table_id": str(table.id)},
    )
    await db_session.commit()
    await db_session.execute(text("ANALYZE documents"))

    requested_ids = [
        f"tenant|drive|item-{item:06d}" for item in range(0, 200_000, 400)
    ]
    captured = []

    def capture_statement(orm_execute_state):
        if orm_execute_state.is_select:
            captured.append(orm_execute_state.statement)

    event.listen(db_session.sync_session, "do_orm_execute", capture_statement)
    try:
        documents, total = await DocumentRepository(db_session, table).query(
            DocumentQuery(
                document_ids=requested_ids,
                where={"tenant_id": "tenant"},
                skip_count=True,
                limit=500,
            )
        )
    finally:
        event.remove(db_session.sync_session, "do_orm_execute", capture_statement)

    assert total == -1
    assert len(documents) == 500
    assert len(captured) == 1
    sql = str(
        captured[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    result = await db_session.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"))
    nodes = list(_plan_nodes(result.scalar_one()[0]["Plan"]))

    assert any(
        node["Node Type"] in {"Index Scan", "Index Only Scan", "Bitmap Index Scan"}
        and node.get("Index Name") == "documents_pkey"
        for node in nodes
    ), nodes
