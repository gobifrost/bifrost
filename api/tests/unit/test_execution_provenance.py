"""Unproven durable origin never grants a human policy exception."""

from uuid import uuid4

import pytest

from shared.execution_provenance import durable_execution_provenance, execution_provenance


@pytest.mark.parametrize("origin,agent", [(None, None), ("agent", None), ("agent", "bad"), ("human", "bad"), ("invalid", None)])
def test_unproven_durable_origin_is_unknown(origin, agent):
    result = durable_execution_provenance(origin, agent)
    assert (result.origin, result.agent_id) == ("unknown", None)


def test_agent_origin_requires_and_keeps_a_valid_agent():
    agent_id = uuid4()
    result = execution_provenance("agent", str(agent_id))
    assert (result.origin, result.agent_id) == ("agent", agent_id)
    with pytest.raises(ValueError):
        execution_provenance("human", agent_id)


def test_direct_human_origin_has_no_agent():
    result = execution_provenance("human", None)
    assert (result.origin, result.agent_id) == ("human", None)
