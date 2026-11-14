"""Observe upstream setup-failure consumption; mocks are not database proof."""
import asyncio
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
from src.services.authorization.tokens import RunCredentials
from src.services.execution.process_pool import CredentialRoute

MODULE = 'src.jobs.consumers.workflow_execution'
RUN = '00000000-0000-0000-0000-000000000001'
WORKER = UUID('00000000-0000-0000-0000-000000000002')


async def observe(case):
    consumer = WorkflowExecutionConsumer.__new__(WorkflowExecutionConsumer)
    consumer.owner_worker_id = WORKER
    consumer._redis_client = AsyncMock()
    consumer._pool = AsyncMock()
    consumer._pool.credential_route = Mock(return_value=CredentialRoute(2, 1))
    consumer._pool.route_execution.side_effect = ValueError('Synthetic known prelaunch refusal')
    update = AsyncMock(return_value=case == 'write_accepted')
    if case == 'write_raises':
        update.side_effect = ConnectionError('Synthetic uncertain canonical write')
    credentials = RunCredentials('synthetic-unusable-access', 'synthetic-unusable-refresh',
                                 datetime.now(timezone.utc), 2)
    error_type, returned = None, False
    with (
        patch(f'{MODULE}.create_execution', new=AsyncMock()),
        patch(f'{MODULE}.update_execution', new=update),
        patch(f'{MODULE}.publish_execution_update', new=AsyncMock()) as publish,
        patch(f'{MODULE}.publish_history_update', new=AsyncMock()) as history,
        patch('src.services.authorization.tokens.issue_run_credentials', new=AsyncMock(return_value=credentials)),
    ):
        try:
            result = await consumer.process_message(
                {'execution_id': RUN, 'code': 'eA==', 'script_name': 'synthetic', 'sync': True},
                {'parameters': {}, 'org_id': None, 'user_id': None,
                 'user_name': 'Synthetic', 'user_email': 'synthetic@example.invalid'})
            returned = result is None
        except ConnectionError as error:
            error_type = type(error).__name__
    call = update.await_args
    if call is None or consumer._pool.route_execution.await_count != 1:
        raise RuntimeError('Probe did not reach the intended upstream setup-failure seam')
    return {
        'case': case, 'returned_none': returned, 'error_type': error_type,
        'update_owner_argument_present': 'owner_worker_id' in call.kwargs,
        'execution_id_matches': call.kwargs['execution_id'] == RUN,
        'failure_publications': publish.await_count,
        'history_publications': history.await_count,
        'sync_results': consumer._redis_client.push_result.await_count,
    }


async def main():
    observations = [await observe(case) for case in ('write_accepted', 'write_refused', 'write_raises')]
    print(json.dumps({'scope': 'actual upstream consumer with mocked persistence/publication/credentials',
        'database_tested': False, 'cloud_invocations': 0, 'observations': observations}))


if __name__ == '__main__':
    asyncio.run(main())
