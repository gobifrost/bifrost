"""Authorized historical tool evidence reaches the queued Test Designer."""
from uuid import UUID, uuid4

import pytest

from src.models.orm.agent_runs import AgentRun, AgentRunStep, AgentToolInvocation
from tests.e2e.api.test_agent_evaluation_api import studio_llm_profile as studio_llm_profile

pytestmark = pytest.mark.asyncio


async def _suite(e2e_client, headers, profile_id):
    agent = e2e_client.post('/api/agents', headers=headers, json={
        'name': f'History Agent {uuid4().hex[:8]}',
        'system_prompt': 'Return a short acknowledgement.',
        'access_level': 'authenticated', 'llm_profile_id': str(profile_id),
        'max_run_timeout': 5,
    })
    assert agent.status_code == 201, agent.text
    response = e2e_client.post('/api/agent-evaluations/suites', headers=headers, json={
        'name': f'history-{uuid4().hex[:8]}', 'agent_id': agent.json()['id'],
    })
    assert response.status_code == 200, response.text
    return UUID(agent.json()['id']), response.json()['id']


async def test_designer_receives_selected_durable_and_legacy_tool_shapes(
    e2e_client, platform_admin, db_session, studio_llm_profile,
):
    agent_id, suite_id = await _suite(e2e_client, platform_admin.headers, studio_llm_profile)
    durable = AgentRun(agent_id=agent_id, trigger_type='api', status='completed',
                       input={'task': 'inspect ticket'}, output={'api_token': 'private-value'})
    legacy = AgentRun(agent_id=agent_id, trigger_type='api', status='completed')
    db_session.add_all([durable, legacy])
    await db_session.flush()
    db_session.add(AgentToolInvocation(
        operation_id=uuid4().hex, run_id=durable.id, provider_tool_call_id='call-1',
        tool_name='get_ticket', arguments={'id': 'ticket-1', 'password': 'private-value'},
        result={'id': 'ticket-1', 'secret': 'private-value'}, state='completed',
        idempotency_key=uuid4().hex,
    ))
    db_session.add(AgentRunStep(
        run_id=legacy.id, step_number=1, type='tool_result', content={
            'tool_name': 'get_ticket',
            'result': '{"id":"ticket-2","api_key":"private-value"}',
        },
    ))
    durable_id, legacy_id = durable.id, legacy.id
    await db_session.commit()
    response = e2e_client.post(
        f'/api/agent-evaluations/suites/{suite_id}/designer/drafts',
        headers=platform_admin.headers, json={
            'suite_goal': 'Cover varied historical response shapes and failures.',
            'requested_count': 1, 'historical_run_ids': [str(durable_id), str(legacy_id)],
        },
    )
    assert response.status_code == 200, response.text
    designer = await db_session.get(AgentRun, UUID(response.json()['run_id']))
    assert designer is not None
    assert designer.execution_snapshot['model']['profile_id'] == str(studio_llm_profile)
    by_id = {item['run_id']: item for item in designer.input['historical_examples']}
    evidence = by_id[str(durable_id)]
    assert evidence['output']['api_token'] == '[REDACTED]'
    assert evidence['tool_calls'][0]['arguments'] == {'id': 'ticket-1', 'password': '[REDACTED]'}
    assert evidence['tool_calls'][0]['result'] == {'id': 'ticket-1', 'secret': '[REDACTED]'}
    assert by_id[str(legacy_id)]['tool_calls'][0]['result'] == {
        'id': 'ticket-2', 'api_key': '[REDACTED]',
    }


async def test_designer_rejects_history_outside_suite_tenant(
    e2e_client, platform_admin, db_session, studio_llm_profile, org1,
):
    agent_id, suite_id = await _suite(e2e_client, platform_admin.headers, studio_llm_profile)
    history = AgentRun(agent_id=agent_id, org_id=UUID(org1['id']), trigger_type='api', status='completed')
    db_session.add(history)
    await db_session.flush()
    history_id = history.id
    await db_session.commit()
    response = e2e_client.post(
        f'/api/agent-evaluations/suites/{suite_id}/designer/drafts',
        headers=platform_admin.headers, json={
            'suite_goal': 'Do not disclose another tenant.', 'requested_count': 1,
            'historical_run_ids': [str(history_id)],
        },
    )
    assert response.status_code == 404, response.text
