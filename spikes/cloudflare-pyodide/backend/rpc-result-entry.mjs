// Fixed local lifecycle probe. No workflow, identity, outbound access or admission.
import {feature} from './guest-disposal-entry.mjs';
const python = `
from workers import WorkerEntrypoint
import asyncio
import sys, sysconfig, pyodide
state = {"started": 0, "after": 0}
gate = asyncio.Event()
class Default(WorkerEntrypoint):
    async def abi(self):
        return {"pyodide": pyodide.__version__, "python": list(sys.version_info[:3]),
                "cache_tag": sys.implementation.cache_tag,
                "soabi": sysconfig.get_config_var("SOABI"), "platform": sysconfig.get_platform()}
    async def result(self):
        return {"value": 7}
    async def primitive(self):
        return 7
    async def slow(self):
        state["started"] += 1
        await asyncio.sleep(0.15)
        state["after"] += 1
        return {"value": 7}
    async def status(self):
        return dict(state)
    async def held(self):
        state["started"] += 1
        await gate.wait()
        state["after"] += 1
        return {"value": 7}
    async def release(self):
        gate.set()
        return True
`;
function shape(value) {
  return {tag: Object.prototype.toString.call(value), is_map: value instanceof Map,
    own_keys: Object.keys(value).slice(0, 8)};
}
function field(value, name) {
  // This fixture intentionally qualifies one concrete RPC conversion contract.
  // Do not silently substitute JSON-object or proxy behavior.
  if (!(value instanceof Map)) throw new TypeError('Expected Python RPC dictionary as Map');
  return value.get(name);
}
function snapshot(value) { return {started: field(value, 'started'), after: field(value, 'after')}; }
function dispose(value) {
  if (!feature(value, Symbol.dispose).available) return false;
  value[Symbol.dispose]();
  return true;
}
export default {
  async fetch(request, env) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    const worker = env.LOADER.load({compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
      mainModule: 'main.py', modules: {'main.py': {py: python}}, env: {}, globalOutbound: null,
      limits: {cpuMs: 1000, subRequests: 0}});
    const entrypoint = worker.getEntrypoint();
    const abiResult = await entrypoint.abi();
    const abi = Object.fromEntries(['pyodide', 'python', 'cache_tag', 'soabi', 'platform']
      .map(name => [name, field(abiResult, name)]));
    dispose(abiResult);
    const result = await entrypoint.result();
    const resultShape = shape(result);
    const value = field(result, 'value');
    const resultFeatures = feature(result, Symbol.dispose);
    const resultDisposed = dispose(result);
    const promise = entrypoint.result();
    const promiseFeatures = feature(promise, Symbol.dispose);
    const second = await promise;
    const promiseValue = field(second, 'value');
    // Separate calls avoid double disposal of aliases to the same result.
    const promiseDisposed = dispose(promise);
    const primitive = await entrypoint.primitive();
    const control = await entrypoint.slow();
    dispose(control);
    const controlStatus = await entrypoint.status();
    const before = snapshot(controlStatus);
    dispose(controlStatus);
    const pending = entrypoint.held();
    const pendingFeatures = feature(pending, Symbol.dispose);
    const atStatus = await entrypoint.status();
    const atDisposal = snapshot(atStatus);
    dispose(atStatus);
    const pendingDisposed = dispose(pending);
    // Causal ordering: completion is impossible until this post-disposal RPC.
    const releasedAfterDisposal = await entrypoint.release();
    let outcome;
    try {
      const returned = await pending;
      outcome = {returned: true, value: field(returned, 'value')};
      // Only dispose this resolved object if the promise was not disposed.
      if (!pendingDisposed) dispose(returned);
    } catch (error) { outcome = {returned: false, error_type: error.name}; }
    await new Promise(resolve => setTimeout(resolve, 250));
    const afterStatus = await entrypoint.status();
    const after = snapshot(afterStatus);
    dispose(afterStatus);
    return Response.json({abi, value, result_shape: resultShape, result_features: resultFeatures, result_disposed: resultDisposed,
      promise_value: promiseValue, promise_features: promiseFeatures, promise_disposed: promiseDisposed,
      primitive, primitive_features: feature(primitive, Symbol.dispose), before,
      at_disposal: atDisposal, pending_features: pendingFeatures, pending_disposed: pendingDisposed,
      released_after_disposal: releasedAfterDisposal, pending_outcome: outcome, after, physical_termination_verified: false,
      note: 'Object disposal and pending-work observations are not isolate destruction evidence'});
  },
};
