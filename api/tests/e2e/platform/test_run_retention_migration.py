"""Schema produced by 20261006_run_retention.

Usage and rollup rows must outlive the runs they describe, so ai_usage carries
no foreign keys to executions or agent_runs, and the daily rollup table treats
NULL organization/workflow as one identity.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.e2e

_INSERT_ROLLUP = text(
    "INSERT INTO workflow_run_daily "
    "(day, organization_id, workflow_id, workflow_name, status, run_count, total_duration_ms, total_cpu_seconds) "
    "VALUES (:day, NULL, NULL, 'synthetic-workflow', 'Success', 1, 10, 0.5)"
)


@pytest.mark.asyncio
async def test_ai_usage_has_no_run_foreign_keys(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT a.attname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
            "WHERE c.contype = 'f' AND c.conrelid = 'ai_usage'::regclass"
        )
    )
    foreign_key_columns = {row[0] for row in result}
    assert "execution_id" not in foreign_key_columns
    assert "agent_run_id" not in foreign_key_columns
    assert "conversation_id" in foreign_key_columns


@pytest.mark.asyncio
async def test_columns_added_and_dropped(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_name IN ('ai_usage', 'execution_metrics_daily')"
        )
    )
    columns = {(row[0], row[1]) for row in result}
    assert ("ai_usage", "workflow_id") in columns
    assert ("ai_usage", "agent_id") in columns
    assert ("execution_metrics_daily", "total_ai_cost") not in columns
    assert ("execution_metrics_daily", "total_ai_calls") not in columns
    assert ("execution_metrics_daily", "total_ai_input_tokens") not in columns
    assert ("execution_metrics_daily", "total_ai_output_tokens") not in columns


@pytest.mark.asyncio
async def test_paging_indexes_are_valid(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT c.relname, i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname IN ('ix_executions_completed_id', 'ix_agent_runs_completed_id')"
        )
    )
    assert dict(result.all()) == {
        "ix_executions_completed_id": True,
        "ix_agent_runs_completed_id": True,
    }


@pytest.mark.asyncio
async def test_daily_rollup_key_is_nulls_not_distinct(db_session: AsyncSession) -> None:
    day = date(2020, 1, 1)
    await db_session.execute(_INSERT_ROLLUP, {"day": day})
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(_INSERT_ROLLUP, {"day": day})
    await db_session.execute(text("DELETE FROM workflow_run_daily WHERE workflow_name = 'synthetic-workflow'"))
