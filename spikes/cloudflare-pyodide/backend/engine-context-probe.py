"""Credential-free engine/context closure probe, not SDK/auth acceptance."""
import json
from workers import WorkerEntrypoint, Response
from runtime_guard import require_runtime
from src.sdk.context import Caller, ExecutionContext
from src.services.execution.engine import ExecutionRequest, execute


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        mode = json.loads(await request.text())['mode']
        async def workflow(context: ExecutionContext):
            import asyncio
            await asyncio.sleep(0)
            return {'value': sum(range(8)), 'public_url': context.public_url,
                    **({key: getattr(context, key) for key in (
                        'is_agent', 'execution_origin', 'agent_id',
                        'original_user_is_platform_admin', 'original_user_id')} if mode != 'baseline' else {})}
        identity = {} if mode == 'baseline' else {
            'is_agent': True, 'execution_origin': 'agent', 'agent_id': 'synthetic-agent',
            'original_user_is_platform_admin': True, 'original_user_id': 'synthetic-original-user'}
        try:
            result = await execute(ExecutionRequest(execution_id='engine-context-probe',
                caller=Caller('user', 'user@example.invalid', 'User'), organization=None,
                func=workflow, transient=True, no_cache=True, public_url='https://isolated.example.invalid', **identity))
            return Response.json({'runtime': require_runtime(), 'status': result.status.value,
                                  'result': result.result, 'error_type': result.error_type})
        except Exception as exc:
            return Response.json({'runtime': require_runtime(), 'error_type': type(exc).__name__})
