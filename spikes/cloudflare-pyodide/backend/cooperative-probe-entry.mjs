// Credential-free local RPC mechanism probe. Never deploy this entrypoint.
import cooperativeSource from './cooperative-code.mjs';

const python = `
import asyncio, json, sys, sysconfig
from workers import WorkerEntrypoint
from cooperative_execution import execute_cooperatively, request_cancel, active_execution_count
entered = asyncio.Event()
release = asyncio.Event()
class Default(WorkerEntrypoint):
    async def wait_entered(self):
        await entered.wait()
        return True
    async def release_execution(self):
        release.set()
        return True
    async def cancel_execution(self, execution_id):
        return request_cancel(execution_id)
    async def run_execution(self, payload):
        data = json.loads(payload)
        cleaned = []
        async def execute():
            entered.set()
            try:
                await release.wait()
                return {"status": "Success", "result": "normal"}
            except asyncio.CancelledError:
                if data["suppress"]:
                    return {"status": "Success", "result": "suppressed"}
                raise
            finally:
                cleaned.append(True)
        outcome = await execute_cooperatively(data["execution_id"], execute)
        return json.dumps({"outcome": outcome, "cleaned": cleaned,
            "active_slots": active_execution_count(), "python": sys.version,
            "abi": sysconfig.get_config_var("SOABI")})
`;

export default {
  async fetch(request, env) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') {
      return new Response('Local POST only', {status: 403});
    }
    const cases = [];
    for (const mode of ['normal', 'cancel', 'suppress']) {
      const executionId = crypto.randomUUID();
      const child = env.LOADER.load({compatibilityDate: '2026-10-30',
        compatibilityFlags: ['python_workers'], mainModule: 'main.py',
        modules: {'main.py': {py: python}, 'cooperative_execution.py': {py: cooperativeSource}},
        env: {}, globalOutbound: null, limits: {cpuMs: 1000, subRequests: 8}});
      const entry = child.getEntrypoint();
      const pending = entry.run_execution(JSON.stringify({execution_id: executionId, suppress: mode === 'suppress'}));
      try {
        const entered = await entry.wait_entered();
        const wrongRun = await entry.cancel_execution('unrelated-run');
        const requested = mode === 'normal' ? false : await entry.cancel_execution(executionId);
        if (mode === 'normal' || !requested) await entry.release_execution();
        const body = JSON.parse(await pending);
        const lateCancel = await entry.cancel_execution(executionId);
        cases.push({mode, entered, wrongRun, requested, lateCancel, status: 200, body});
      } finally {
        await entry.release_execution();
      }
    }
    return Response.json({scope: 'raw Pyodide cooperative RPC only', cases,
      engine_sdk_execution_verified: false, physical_stop_verified: false});
  },
};
