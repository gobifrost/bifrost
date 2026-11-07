// Local fixed qualification only; no workflow code, JWT or business-job admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {createAttemptEgress} from './attempt-egress.mjs';
const routes = new Map();
async function observe(id, event, status) {
  const response = await fetch('http://127.0.0.1:8825/events', {method: 'POST',
    body: JSON.stringify({id, event, status}), signal: AbortSignal.timeout(2000)});
  await response.body?.cancel();
  if (response.status !== 204) throw new Error('Observer refused');
}
export class Effects extends WorkerEntrypoint {
  async call(event) {
    if (!['before', 'after'].includes(event)) throw new TypeError('Fixed event only');
    const route = routes.get(this.ctx.props.id);
    if (!route) return 410;
    if (event === 'before' && !route.gate) {
      // Fixed fixture starts its short effect window after Python startup.
      // Production deadline must come from authenticated canonical dispatch.
      route.gate = createAttemptEgress(Date.now() + (route.mode === 'completion' ? 5000 : 100),
        async () => {await observe(this.ctx.props.id, 'effect', 200); return new Response(null, {status: 204});});
    }
    const response = await route.gate.fetch(new Request('https://effect.invalid/', {method: 'POST'}));
    await observe(this.ctx.props.id, event, response.status);
    return response.status;
  }
}
const python = `
import json, sys, sysconfig, pyodide
from workers import WorkerEntrypoint, Response
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        mode = json.loads(await request.text())["mode"]
        before = await self.env.EFFECTS.call("before")
        total = 0
        if mode == "late":
            for value in range(20000000):
                total += value
        statuses = []
        for _ in range(3 if mode == "late" else 1):
            status = None
            try:
                status = await self.env.EFFECTS.call("after")
                if status == 410:
                    raise RuntimeError("synthetic capability expired")
            except BaseException:
                statuses.append(status)
            else:
                statuses.append(status)
        return Response.json({"before": before, "statuses": statuses, "total": total,
            "continued_after_rejections": mode == "late",
            "abi": {"python": list(sys.version_info[:3]), "pyodide": pyodide.__version__,
                    "soabi": sysconfig.get_config_var("SOABI")}})
`;
export default {
  async fetch(request, env, ctx) {
    if (env.LOCAL_PROBE !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    const {id, mode} = await request.json();
    if (!/^[a-f0-9-]{36}$/.test(id) || !['completion', 'late'].includes(mode)) return new Response('Fixed fixture only', {status: 422});
    const route = {mode, gate: null}; routes.set(id, route);
    try {
      const child = env.LOADER.load({compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
        mainModule: 'main.py', modules: {'main.py': {py: python}},
        env: {EFFECTS: ctx.exports.Effects({props: {id}})}, globalOutbound: null,
        limits: {cpuMs: 1000, subRequests: 4}});
      const response = await child.getEntrypoint().fetch('https://attempt.invalid/', {method: 'POST', body: JSON.stringify({mode})});
      return Response.json(await response.json());
    } finally {route.gate?.close(); routes.delete(id);}
  },
};
