// Credential-free local transport mechanism fixture. Never deploy/admit jobs.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {createApprovedHttpsCandidate} from './approved-https-candidate.mjs';
import {invocationStream} from './invocation-stream.mjs';
const attempts = new Map();
export class DisconnectGateway extends WorkerEntrypoint {
  fetch(request) {
    return attempts.get(this.ctx.props.id)?.policy.fetch(request) ?? new Response('Closed', {status: 410});
  }
}
const python = `
from workers import WorkerEntrypoint, Response
from pyodide.http import pyfetch
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        statuses = []
        for path in ("before", "hold", "after"):
            response = await pyfetch("https://vendor.example/" + path, method="POST")
            statuses.append(response.status)
            await response.string()
        return Response.json({"statuses": statuses})
`;
export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true') return new Response('Local only', {status: 403});
    const url = new URL(request.url), id = url.searchParams.get('id');
    if (request.method === 'GET' && url.pathname === '/status') {
      const state = attempts.get(id);
      return state ? Response.json({closed: state.closed, execute_returned: state.returned}) : new Response('Unknown', {status: 404});
    }
    if (request.method !== 'POST' || !/^[a-f0-9-]{36}$/.test(id ?? '') || attempts.has(id) || attempts.size >= 2) {
      return new Response('Fixed two-run fixture only', {status: 403});
    }
    const state = {closed: false, returned: false};
    state.policy = createApprovedHttpsCandidate({origins: ['https://vendor.example'],
      deadline: Date.now() + 30000, maxRequests: 3, forward: outgoing => {
        const path = new URL(outgoing.url).pathname;
        if (!['/before', '/hold', '/after'].includes(path)) return new Response('Fixed path only', {status: 403});
        return fetch(`http://127.0.0.1:8820${path}?id=${id}`, {method: 'POST', redirect: 'manual'});
      }});
    attempts.set(id, state);
    const child = env.LOADER.load({compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
      mainModule: 'main.py', modules: {'main.py': {py: python}}, env: {},
      globalOutbound: ctx.exports.DisconnectGateway({props: {id}}), limits: {cpuMs: 1000, subRequests: 8}});
    return invocationStream({runtimeHandle: `disconnect:${id}`, executionId: id,
      cleanup() {state.closed = true; state.policy.close();},
      async execute() {
        const response = await child.getEntrypoint().fetch('https://synthetic.invalid/');
        const value = await response.json(); state.returned = true;
        return {result: {execution_id: id, success: true, result: value}};
      }});
  },
};
