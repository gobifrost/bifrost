// Fixed local RPC cancellation experiment; no identities or workflow admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {workerCode} from './isolation-code.mjs';
const python = `
from workers import WorkerEntrypoint

class Default(WorkerEntrypoint):
    async def run(self, mode):
        await self.env.EFFECTS.record("before")
        total = 0
        try:
            for value in range(200000000 if mode == "catch" else 20000000):
                total += value
        except BaseException:
            await self.env.EFFECTS.record("caught")
        await self.env.EFFECTS.record("after")
        return {"completed": True}
`;
export class Effects extends WorkerEntrypoint {
  async record(event) {
    if (!['before', 'caught', 'after'].includes(String(event))) throw new TypeError('Fixed event required');
    const response = await fetch('http://127.0.0.1:8820/effect', {
      method: 'POST', body: JSON.stringify({id: this.ctx.props.id, event: String(event)}),
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) throw new Error('Independent observer refused event');
  }
}
export default {
  async fetch(request, env, ctx) {
    if (env.LOCAL_PROBE !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    const {id, mode} = await request.json();
    if (!/^[a-f0-9-]{36}$/.test(id) || !['control', 'abort', 'catch'].includes(mode)) {
      return new Response('Fixed fixture required', {status: 422});
    }
    const code = workerCode('synthetic-rpc-termination', ctx.exports.Effects({props: {id}}));
    code.modules = {'main.py': {py: python}};
    const child = env.LOADER.load(code).getEntrypoint();
    // RPC execution-context cancellation, rather than fetch signal forwarding.
    return Response.json(await child.run(mode));
  },
};
