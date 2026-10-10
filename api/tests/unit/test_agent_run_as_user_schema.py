"""agent_runs.run_as_user_id has a partial index for user deletes."""
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_run_as_user_id_has_partial_index(db_session):
    result = await db_session.execute(text("""
        SELECT indexdef FROM pg_indexes
        WHERE tablename = 'agent_runs' AND indexname = 'ix_agent_runs_run_as_user_id'
    """))
    indexdef = result.scalar_one_or_none()
    assert indexdef is not None
    assert indexdef.endswith("(run_as_user_id) WHERE (run_as_user_id IS NOT NULL)")
