import {WorkerEntrypoint} from 'cloudflare:workers';
import {runCpuPlacement} from './cpu-placement.mjs';
import {summarizeTail} from './tail-observer.mjs';
const active = new Map();
async function record(id, event, metadata = undefined) {
  const response = await fetch('http://127.0.0.1:8819/events', {method: 'POST', redirect: 'manual',
    body: JSON.stringify({id, event, metadata}), signal: AbortSignal.timeout(2000)});
  await response.body?.cancel();
  if (response.status !== 204) throw new Error('Independent observer rejected event');
}
export class Effects extends WorkerEntrypoint {
  async record(event) {
    if (!['before', 'after', 'caught', 'background_after'].includes(event)) throw new TypeError('Child event only');
    // Continue reporting late events after collection ends; never discard them
    // merely because the parent's active route was removed.
    await record(this.ctx.props.id, event);
    if (event === 'before') active.get(this.ctx.props.id)?.started();
  }
}
export class NativeTail extends WorkerEntrypoint {
  async tail(events) {
    await record(this.ctx.props.id, 'native_tail', summarizeTail(events));
    active.get(this.ctx.props.id)?.finished();
  }
}
const javascript = `export default {async fetch(request,env,ctx) {
  const {mode}=await request.json();
  await env.EFFECTS.record('before');
  console.log('synthetic-tail-secret');
  if(mode==='js_background') {
    ctx.waitUntil(new Promise(resolve=>setTimeout(resolve,150)).then(()=>env.EFFECTS.record('background_after')));
  } else {
    if(mode==='js_io_abort') await new Promise(resolve=>setTimeout(resolve,500));
    await env.EFFECTS.record('after');
  }
  return Response.json({completed:true});
}};`;
export default {
  async fetch(request, env, ctx) {
    if (env.LOCAL_PROBE !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    const {id, mode} = await request.json();
    if (!/^[a-f0-9-]{36}$/.test(id) || !['js_completion', 'js_background', 'js_io_abort', 'python_io', 'python_cpu'].includes(mode)) {
      return new Response('Fixed diagnostic only', {status: 422});
    }
    let started;
    const before = new Promise(resolve => {started = resolve;});
    let finished;
    const tailed = new Promise(resolve => {finished = resolve;});
    const route = {started, finished, worker: null};
    active.set(id, route);
    // Bounded observer/handle retention, not a stop deadline or run authority.
    ctx.waitUntil(Promise.race([tailed, new Promise(resolve => setTimeout(resolve, 5000))])
      .finally(() => active.delete(id)));
    const effects = ctx.exports.Effects({props: {id}});
    const tails = [ctx.exports.NativeTail({props: {id}})];
    const controller = new AbortController();
    try {
      let running;
      if (mode.startsWith('python_')) {
        const loader = {load(code) {route.worker = env.LOADER.load({...code, tails}); return route.worker;}};
        running = runCpuPlacement(loader, effects, mode === 'python_io' ? 'completion' : 'code_limit');
      } else {
        const worker = env.LOADER.load({compatibilityDate: '2026-10-30', mainModule: 'main.js',
          modules: {'main.js': {js: javascript}}, env: {EFFECTS: effects}, globalOutbound: null,
          limits: {cpuMs: 1000, subRequests: 4}, tails});
        route.worker = worker;
        running = worker.getEntrypoint().fetch('https://attempt.invalid/', {method: 'POST',
          headers: {Authorization: 'Bearer synthetic-tail-secret'}, body: JSON.stringify({mode}), signal: controller.signal})
          .then(response => ({response}), error => ({error_type: error.name}));
      }
      if (mode === 'js_io_abort') {
        await before;
        await new Promise(resolve => setTimeout(resolve, 10));
        controller.abort();
        await record(id, 'parent_abort_requested');
      }
      try {
        const outcome = await running;
        if (mode.startsWith('python_')) return Response.json({outcome});
        return outcome.response ? Response.json({outcome: await outcome.response.json()}) : Response.json(outcome);
      } catch (error) { return Response.json({error_type: error.name}); }
    } finally { if (!route.worker) finished(); }
  },
};
