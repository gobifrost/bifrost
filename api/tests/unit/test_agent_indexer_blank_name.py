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
