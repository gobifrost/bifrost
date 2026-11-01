import {WorkerEntrypoint} from 'cloudflare:workers';
import {workerCode} from './isolation-code.mjs';

// Bounded diagnostic observations, never execution claims or a business queue.
const observations = new Map();
export class Effects extends WorkerEntrypoint {
  record(event) {
    const value = String(event);
    if (!['before','after','caught'].includes(value)) throw new TypeError('Invalid probe event');
    const observation = observations.get(this.ctx.props.id);
    if (observation) {
      observation.records.push(value);
      if (value === 'before') observation.started();
    }
  }
}

export default {
  async fetch(request, env, ctx) {
    if (env.LOCAL_PROBE !== 'true' && (!env.SPIKE_AUTH ||
        !(Date.now() < Number(env.SPIKE_EXPIRES_AT)) ||
        request.headers.get('X-Spike-Auth') !== env.SPIKE_AUTH)) {
      return new Response('Private diagnostic only', {status:403});
    }
    if (request.method !== 'POST') return new Response('POST required', {status:405});
    const body = await request.json();
    if (!['state','io','cpu','cpu_limit','cpu_catch','egress'].includes(body.action)) return new Response('Unsupported', {status:422});
    if (observations.size >= 8) return new Response('Probe capacity exceeded', {status:429});
    const id = crypto.randomUUID();
    const records = [];
    const controller = new AbortController();
    let timer;
    let started;
    const ready = new Promise(resolve=>{started=resolve;});
    observations.set(id, {records,started});
    try {
      const code = workerCode(`synthetic-${id}`, ctx.exports.Effects({props:{id}}));
      // Never use get(id): every attempt requires a fresh interpreter.
      const worker = env.LOADER.load(code);
      let outcome;
      try {
        const running = worker.getEntrypoint().fetch(new Request('https://attempt.invalid/', {
          method:'POST', body:JSON.stringify({action:body.action,marker:body.marker ?? id}), signal:controller.signal,
        }));
        if(body.cancel) {
          await Promise.race([ready,running]);
          timer=setTimeout(()=>controller.abort('diagnostic cancellation'),5);
        }
        const response = await running;
        outcome = {status:response.status,body:await response.json()};
      } catch(error) { outcome = {error_type:error.name,error:String(error)}; }
      finally { if(timer) clearTimeout(timer); }
      // Observe whether canceled code continues after the caller receives abort.
      if(body.cancel) await new Promise(resolve=>setTimeout(resolve,400));
      return Response.json({revision:5,outcome, effects:records,abort_requested:controller.signal.aborted,
        parent_canary_configured:env.PARENT_CANARY === 'synthetic-supervisor-only',
        cancel_input:body.cancel ?? false});
    } finally { observations.delete(id); }
  },
};
