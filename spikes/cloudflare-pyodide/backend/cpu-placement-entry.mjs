import {WorkerEntrypoint} from 'cloudflare:workers';
import {CPU_CASES, runCpuPlacement} from './cpu-placement.mjs';

export class Effects extends WorkerEntrypoint {
  async record(event) {
    if (!['before', 'caught', 'after'].includes(event)) throw new TypeError('Invalid diagnostic event');
    const response = await fetch('http://127.0.0.1:8815/events', {
      method: 'POST', redirect: 'manual',
      body: JSON.stringify({case_id: this.ctx.props.caseId, event}),
    });
    await response.body?.cancel();
    if (response.status !== 204) throw new Error('Independent local observer rejected event');
  }
}

export default {
  async fetch(request, env, ctx) {
    if (env.LOCAL_PROBE !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    const caseId = new URL(request.url).pathname.slice(1);
    if (!CPU_CASES.includes(caseId)) return new Response('Unsupported diagnostic', {status: 422});
    return Response.json(await runCpuPlacement(env.LOADER, ctx.exports.Effects({props: {caseId}}), caseId));
  },
};
