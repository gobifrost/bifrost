// Candidate composition only. Does not enable dispatch or claim terminal cancellation.
export function withCooperativeRpc(code, cooperativeSource) {
  const original = code.modules?.['main.py']?.py;
  if (typeof original !== 'string' || typeof cooperativeSource !== 'string') {
    throw new Error('Python runtime and cancellation source required');
  }
  let python = original;
  function replace(before, after) {
    if (python.split(before).length !== 2) throw new Error('Pinned runtime entry shape changed');
    python = python.replace(before, after);
  }
  replace('class Default(WorkerEntrypoint):',
    'cleanup_evidence = {"sdk_close_returned": False}\n\nclass EngineEntrypoint(WorkerEntrypoint):');
  replace('    async def fetch(self, request):\n        body = json.loads(await request.text())',
    '    async def _execute_body(self, body):');
  replace('            await close_sdk(get_client())',
    '            await close_sdk(get_client())\n            cleanup_evidence["sdk_close_returned"] = True');
  python += `
from cooperative_execution import execute_cooperatively, request_cancel

class Default(EngineEntrypoint):
    async def fetch(self, request):
        return Response("Candidate RPC only", status=403)

    async def cancel_execution(self, execution_id):
        return request_cancel(execution_id)

    async def run_execution(self, payload):
        body = json.loads(payload)
        if body.get("action") not in ("execute", "execute_envelope"):
            raise ValueError("Candidate execution action required")
        execution_id = body["execution_id"]
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("Execution identity required")

        async def execute():
            cleanup_evidence["sdk_close_returned"] = False
            response = await self._execute_body(body)
            # Consume the response while still in the execution invocation.
            return {"kind": "engine_result", "body": json.loads(await response.text())}

        outcome = await execute_cooperatively(execution_id, execute, owner_scheduled=True)
        return json.dumps({"outcome": outcome,
            "sdk_close_returned": cleanup_evidence["sdk_close_returned"]})
`;
  return {...code, modules: {...code.modules, 'main.py': {py: python},
    'cooperative_execution.py': {py: cooperativeSource}}};
}
