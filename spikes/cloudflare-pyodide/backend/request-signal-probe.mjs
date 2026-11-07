// Local fixed diagnostic. Neither a workflow executor nor a physical-stop receipt.
const python = `
import asyncio, json, sys, sysconfig, pyodide
from workers import WorkerEntrypoint, Response
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        mode = json.loads(await request.text())["mode"]
        await self.env.EFFECTS.record("before")
        try:
            if mode != "cpu_abort":
                await asyncio.sleep(0.25)
            else:
                total = 0
                for value in range(20000000):
                    total += value
        except BaseException as exc:
            await self.env.EFFECTS.record("caught")
        await self.env.EFFECTS.record("after")
        return Response.json({"python": list(sys.version_info[:3]), "pyodide": pyodide.__version__,
                              "soabi": sysconfig.get_config_var("SOABI")})
`;
export async function postSignalEvent(id, event) {
  const response = await fetch('http://127.0.0.1:8817/events', {
    method: 'POST', redirect: 'manual', body: JSON.stringify({id, event}), signal: AbortSignal.timeout(2000),
  });
  await response.body?.cancel();
  if (response.status !== 204) throw new Error('Independent observer rejected event');
}
export async function forwardSignalProbe(request, loader, effects, onParentAbort, waitUntil) {
  const body = await request.json();
  if (!body || !/^[a-f0-9-]{36}$/.test(body.id) || !['control', 'io_abort', 'cpu_abort'].includes(body.mode)) {
    return new Response('Fixed diagnostic only', {status: 422});
  }
  request.signal.addEventListener('abort', () => { waitUntil(onParentAbort(body.id)); }, {once: true});
  const worker = loader.load({compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
    mainModule: 'main.py', modules: {'main.py': {py: python}}, env: {EFFECTS: effects(body.id)},
    globalOutbound: null, limits: {cpuMs: 1000, subRequests: 4}});
  // Explicit RequestInit.signal: forwarding a Request object alone requires
  // request_signal_passthrough, a separate flag which this probe does not set.
  return worker.getEntrypoint().fetch('https://attempt.invalid/', {
    method: 'POST', body: JSON.stringify({mode: body.mode}), signal: request.signal,
  });
}
