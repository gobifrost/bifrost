// Diagnostics contain no tokens, source content, exception strings or secrets.
export const pythonEntry = `
import json
from workers import WorkerEntrypoint, Response
from runtime_runner import run_authorized_attempt

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        body = json.loads(await request.text())
        result = await run_authorized_attempt(body['execution_id'], body['context'], body['api_url'],
                                              public_url=body['public_url'])
        from src.core import module_cache_sync
        from runtime_guard import runtime_fingerprint
        diagnostic = {'execution_child': module_cache_sync._execution_child,
                      'engine_client_available': module_cache_sync._get_engine_client() is not None,
                      'abi': runtime_fingerprint()}
        return Response.json({'upstream_result': result, 'bootstrap': diagnostic})
`;
