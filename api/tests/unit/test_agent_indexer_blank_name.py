"""AgentIndexer must reject blank-name agents loudly, not silently no-op.

Regression for the Solutions-deploy 'lying success' bug: a manifest agent with
an empty name was swallowed (index_agent returned False) while deploy still
reported agents_upserted=1.
"""
import pytest
import yaml

from src.services.file_storage.indexers.agent import AgentIndexer


def _yaml(**fields) -> bytes:
    return yaml.dump(fields).encode("utf-8")


@pytest.mark.asyncio
async def test_index_agent_raises_on_blank_name(db_session):
    indexer = AgentIndexer(db_session)
    content = _yaml(id="11111111-1111-1111-1111-111111111111", name="", system_prompt="hi")
    with pytest.raises(ValueError, match="agent name is required"):
        await indexer.index_agent("agents/x.agent.yaml", content)


@pytest.mark.asyncio
async def test_index_agent_raises_on_missing_system_prompt(db_session):
    indexer = AgentIndexer(db_session)
    content = _yaml(id="11111111-1111-1111-1111-111111111111", name="Valid")
    with pytest.raises(ValueError, match="system_prompt is required"):
        await indexer.index_agent("agents/x.agent.yaml", content)


# ---------------------------------------------------------------------------
# Autonomous-run limits (max_iterations / max_token_budget) must round-trip
# through index_agent. The round-trip harness can't pin this directly for the
# indexer-content path, so it is pinned here.
#
# On re-import the on-conflict-update OMITS the limit keys when the manifest
# lacks them (symmetric with the insert branch) rather than coalescing an
# absent value to 50/100000. NOTE: a genuine NULL is not reachable through the
# ORM create/update paths (the column's python-side default=50 fires on a None
# insert), so the NULL-preservation case can't be set up via the ORM
# constructor — the symmetric-omit fix is defensive (raw-SQL / future
# server_default) and is verified by review + the present-value round trip here.
# ---------------------------------------------------------------------------


async def _get_agent(db_session, agent_id: str):
    from uuid import UUID

    from sqlalchemy import select

    from src.models.orm import Agent

    return await db_session.scalar(select(Agent).where(Agent.id == UUID(agent_id)))


@pytest.mark.asyncio
async def test_index_agent_persists_limits_when_present(db_session):
    aid = "22222222-2222-2222-2222-222222222222"
    indexer = AgentIndexer(db_session)
    await indexer.index_agent(
        "agents/a.agent.yaml",
        _yaml(id=aid, name="A", system_prompt="p", max_iterations=7, max_token_budget=8888),
    )
    agent = await _get_agent(db_session, aid)
    assert agent is not None
    assert agent.max_iterations == 7
    assert agent.max_token_budget == 8888
