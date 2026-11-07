// Fixed diagnostic only. No workflow admission or physical-stop certification.
const python = `
import asyncio
import json
import sys
import sysconfig
import pyodide
from workers import WorkerEntrypoint, Response

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        action = json.loads(await request.text())["action"]
        abi = {"python": list(sys.version_info[:3]), "pyodide": pyodide.__version__,
               "soabi": sysconfig.get_config_var("SOABI")}
        await self.env.EFFECTS.record("before")
        if action == "io":
            await asyncio.sleep(0.25)
            await self.env.EFFECTS.record("after")
            return Response.json({"completed": True, "abi": abi})
        total = 0
        try:
            for value in range(200000000):
                total += value
        except BaseException as exc:
            await self.env.EFFECTS.record("caught")
            await self.env.EFFECTS.record("after")
            return Response.json({"caught": type(exc).__name__, "abi": abi})
        await self.env.EFFECTS.record("after")
        return Response.json({"total": total, "abi": abi})
`;

export const CPU_CASES = Object.freeze(['completion', 'code_limit', 'entrypoint_limit']);
const EVENTS = Object.freeze(['before', 'caught', 'after']);
const API_ORIGIN = 'https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev';

export async function recordCpuEffect(api, token, caseId, event) {
  if (!CPU_CASES.includes(caseId) || !EVENTS.includes(event)) throw new TypeError('Invalid diagnostic event');
  // The caller's credential stays in the parent capability, never child env.
  const response = await api.fetch(new Request(`${API_ORIGIN}/api/sdk/runtime/logs`, {
    method: 'POST', redirect: 'manual',
    headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'},
    body: JSON.stringify({level: 'INFO', message: `cpu-placement:${caseId}:${event}`}),
  }));
  const accepted = response.status === 200;
  await response.body?.cancel();
  if (!accepted) throw new Error('Independent observer rejected event');
}

export async function runCpuPlacement(loader, effects, caseId) {
  if (!CPU_CASES.includes(caseId)) throw new TypeError('Unsupported CPU placement case');
  const code = {compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
    mainModule: 'main.py', modules: {'main.py': {py: python}}, env: {EFFECTS: effects},
    globalOutbound: null, limits: {cpuMs: 1000, subRequests: 4}};
  // All cases retain the same code-level budget; only placement changes.
  const worker = loader.load(code); // Fresh interpreter; never get/cache by ID.
  const entrypoint = caseId === 'entrypoint_limit'
    ? worker.getEntrypoint(null, {limits: {cpuMs: 1000, subRequests: 4}})
    : worker.getEntrypoint();
  try {
    const response = await entrypoint.fetch(new Request('https://attempt.invalid/', {
      method: 'POST', body: JSON.stringify({action: caseId === 'completion' ? 'io' : 'cpu_catch'}),
    }));
    return {case_id: caseId, status: response.status, body: await response.json()};
  } catch (error) {
    // Provider exception strings can contain data. Retain only the type.
    return {case_id: caseId, error_type: error.name};
  }
}

export function classifyCpuPlacement(caseId, independentlyReadEvents) {
  if (!CPU_CASES.includes(caseId) || !Array.isArray(independentlyReadEvents) ||
      independentlyReadEvents.some(event => !EVENTS.includes(event))) throw new TypeError('Invalid independent observation');
  const events = independentlyReadEvents;
  const caught = events.indexOf('caught');
  const continued = caught >= 0 && events.indexOf('after', caught + 1) >= 0;
  return {case_id: caseId, events: [...events],
    classification: caseId === 'completion' ? (events.join(',') === 'before,after' ? 'observer_control_passed' : 'observer_control_failed')
      : continued ? 'effect_after_caught_exception' : 'physical_stop_unproven',
    physical_termination_verified: false, execution_admission_enabled: false};
}
