// Supported-object inspection only; no credentials, workflow or admission.
const python = `
from workers import WorkerEntrypoint, Response
import sys, sysconfig, pyodide
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response.json({'python': list(sys.version_info[:3]), 'pyodide': pyodide.__version__,
                              'soabi': sysconfig.get_config_var('SOABI')})
`;
export function feature(object, key) {
  if (typeof key !== 'symbol') return {available: false, symbol_available: false};
  try { return {available: typeof object[key] === 'function'}; }
  catch (error) { return {available: false, error_type: error.name}; }
}
export default {
  async fetch(request, env) {
    if (env.LOCAL_PROBE !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    const worker = env.LOADER.load({compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
      mainModule: 'main.py', modules: {'main.py': {py: python}}, env: {}, globalOutbound: null,
      limits: {cpuMs: 1000, subRequests: 0}});
    const entrypoint = worker.getEntrypoint();
    const features = {
      worker_dispose: feature(worker, Symbol.dispose), worker_async_dispose: feature(worker, Symbol.asyncDispose),
      entrypoint_dispose: feature(entrypoint, Symbol.dispose), entrypoint_async_dispose: feature(entrypoint, Symbol.asyncDispose),
    };
    const response = await entrypoint.fetch(new Request('https://probe.invalid/'));
    const abi = await response.json();
    // Only invoke a disposer the actual supported object advertises; this
    // request is already complete, so no physical-stop claim follows.
    let dispose_returned = false;
    if (features.entrypoint_dispose.available) {
      entrypoint[Symbol.dispose]();
      dispose_returned = true;
    }
    return Response.json({features, abi, completed_call_dispose_returned: dispose_returned,
      physical_termination_verified: false, cloud_invocations: 0});
  },
};
