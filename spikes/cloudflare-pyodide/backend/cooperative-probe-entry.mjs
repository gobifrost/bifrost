// Credential-free local RPC mechanism probe. Never deploy this entrypoint.
import cooperativeSource from './cooperative-code.mjs';
import {WorkerEntrypoint} from 'cloudflare:workers';

const gates = new Map();
export class CooperativeGateway extends WorkerEntrypoint {
  async fetch(request) {
    const gate = gates.get(this.ctx.props.id);
    if (!gate || gate.closed) return new Response('Revoked', {status: 410});
    const url = new URL(request.url);
    if (url.origin !== 'https://vendor.example' || !['/hold', '/after'].includes(url.pathname)
        || request.method !== 'POST' || ++gate.requests > 2) {
      return new Response('Fixed fixture only', {status: 403});
    }
    return fetch(`http://127.0.0.1:8820${url.pathname}?id=${this.ctx.props.id}`, {method: 'POST'});
  }
}

const python = `
import asyncio, json, sys, sysconfig
from workers import WorkerEntrypoint
from pyodide.http import pyfetch
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
                if data["io"]:
                    response = await pyfetch("https://vendor.example/hold", method="POST")
                    await response.string()
                    return {"status": "Success", "result": "normal", "http_status": response.status}
                await release.wait()
                return {"status": "Success", "result": "normal"}
            except asyncio.CancelledError:
                if data["suppress"]:
                    if data["io"]:
                        response = await pyfetch("https://vendor.example/after", method="POST")
                        await response.string()
                        return {"status": "Success", "result": "suppressed", "after_status": response.status}
                    return {"status": "Success", "result": "suppressed"}
                raise
            finally:
                cleaned.append(True)
        outcome = await execute_cooperatively(data["execution_id"], execute,
            owner_scheduled=data["owner_scheduled"])
        return json.dumps({"outcome": outcome, "cleaned": cleaned,
            "active_slots": active_execution_count(), "python": sys.version,
            "abi": sysconfig.get_config_var("SOABI")})
`;

export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') {
      return new Response('Local POST only', {status: 403});
    }
    const cases = [];
    const io = request.headers.get('x-cooperative-io') === 'true';
    const ownerScheduled = request.headers.get('x-owner-scheduled') === 'true';
    const observe = async (path, id) => {
      const response = await fetch(`http://127.0.0.1:8820/${path}?id=${id}`, {method: 'POST'});
      if (response.status !== 200) throw new Error('Independent observer refused control');
      return response.json();
    };
    for (const mode of ['normal', 'cancel', 'suppress']) {
      const executionId = crypto.randomUUID();
      if (io) await observe('new', executionId);
      const gate = {closed: false, requests: 0};
      gates.set(executionId, gate);
      const child = env.LOADER.load({compatibilityDate: '2026-10-30',
        compatibilityFlags: ['python_workers'], mainModule: 'main.py',
        modules: {'main.py': {py: python}, 'cooperative_execution.py': {py: cooperativeSource}},
        env: {}, globalOutbound: io ? ctx.exports.CooperativeGateway({props: {id: executionId}}) : null,
        limits: {cpuMs: 1000, subRequests: 8}});
      const entry = child.getEntrypoint();
      const pending = entry.run_execution(JSON.stringify({execution_id: executionId,
        suppress: mode === 'suppress', io, owner_scheduled: ownerScheduled}));
      let phase = 'wait_entered';
      try {
        const entered = await entry.wait_entered();
        phase = 'wait_destination';
        const held = io ? await observe('wait', executionId) : null;
        const wrongRun = await entry.cancel_execution('unrelated-run');
        if (io && mode !== 'normal') gate.closed = true;
        const requested = mode === 'normal' ? false : await entry.cancel_execution(executionId);
        if (mode === 'normal' || !requested) {
          if (io) await observe('release', executionId);
          else await entry.release_execution();
        }
        phase = 'await_execution_result';
        const body = JSON.parse(await pending);
        phase = 'observe_result';
        const beforeRelease = io ? await observe('snapshot', executionId) : null;
        const lateCancel = await entry.cancel_execution(executionId);
        if (io) await observe('release', executionId);
        const afterRelease = io ? await observe('snapshot', executionId) : null;
        cases.push({mode, entered, wrongRun, requested, lateCancel, status: 200, body,
          execution_id: executionId, held, before_release: beforeRelease, after_release: afterRelease});
      } catch (error) {
        return Response.json({scope: 'raw Pyodide cooperative HTTP cancellation', cases,
          failure: String(error), failed_mode: mode, failed_phase: phase,
          engine_sdk_execution_verified: false, physical_stop_verified: false}, {status: 500});
      } finally {
        if (io) await observe('release', executionId);
        gate.closed = true; gates.delete(executionId);
        await entry.release_execution();
      }
    }
    return Response.json({scope: io ? 'raw Pyodide cooperative HTTP cancellation' : 'raw Pyodide cooperative RPC only', cases,
      engine_sdk_execution_verified: false, physical_stop_verified: false});
  },
};
