// Synthetic local lifecycle experiment. Maps are ephemeral observers, never jobs.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {summarizeTail} from './tail-observer.mjs';
const runs = new Map();
const state = id => {const value = runs.get(id); if (!value) throw new Error('Synthetic observation expired'); return value;};
export class ContextObserver extends WorkerEntrypoint {
  record(event) {
    if (!['started', 'after', 'interrupted', 'bridge_returned', 'bridge_rejected'].includes(event)) throw new TypeError('Fixed event required');
    const run = state(this.ctx.props.id);
    run.events.push(event);
    if (event === 'started') run.started();
    return true;
  }
  async waitStarted() {await state(this.ctx.props.id).start; return true;}
  async gate() {await state(this.ctx.props.id).gate; return true;}
}
export class ContextTail extends WorkerEntrypoint {
  tail(events) {
    const run = state(this.ctx.props.id);
    run.tail = summarizeTail(events);
    run.events.push('caller_tail');
    run.tailed();
  }
}
const python = `
from workers import WorkerEntrypoint
class Default(WorkerEntrypoint):
    async def held(self):
        await self.env.OBSERVER.record("started")
        try:
            await self.env.OBSERVER.gate()
        except BaseException:
            await self.env.OBSERVER.record("interrupted")
            raise
        await self.env.OBSERVER.record("after")
        return 7
`;
export class ContextBridge extends WorkerEntrypoint {
  async run() {
    // Construct and consume the dynamic entrypoint within this invocation only.
    const observer = this.ctx.exports.ContextObserver({props: this.ctx.props});
    const child = this.env.LOADER.load({compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
      mainModule: 'main.py', modules: {'main.py': {py: python}}, env: {OBSERVER: observer},
      globalOutbound: null, limits: {cpuMs: 1000, subRequests: 8}});
    try {
      const result = await child.getEntrypoint().held();
      await observer.record('bridge_returned');
      return result;
    } catch (error) {
      await observer.record('bridge_rejected');
      throw error;
    }
  }
}
const caller = `export default {async fetch(request, env, ctx) {
  if(env.MODE==='control') return Response.json({value:await env.BRIDGE.run()});
  const pending=env.BRIDGE.run();
  // Deliberately unawaited negative control: never a production execution pattern.
  pending.catch(()=>{});
  if(env.MODE==='retained') ctx.waitUntil(pending);
  await env.OBSERVER.waitStarted();
  return Response.json({started:true});
}};`;
async function boundedTail(run) {
  let timer;
  try {return await Promise.race([run.tailReady.then(() => true), new Promise(resolve => {timer=setTimeout(()=>resolve(false),2500);})]);}
  finally {clearTimeout(timer);}
}
export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status:403});
    const ids = [];
    async function exercise(mode) {
      const id=crypto.randomUUID(); ids.push(id);
      const run={events:[],tail:null};
      run.start=new Promise(resolve=>{run.started=resolve;});
      run.gate=new Promise(resolve=>{run.release=resolve;});
      run.tailReady=new Promise(resolve=>{run.tailed=resolve;});
      runs.set(id,run);
      if(mode==='control') {run.events.push('release');run.release();}
      const props={id};
      const child=env.LOADER.load({compatibilityDate:'2026-10-30',mainModule:'main.js',modules:{'main.js':{js:caller}},
        env:{MODE:mode,BRIDGE:ctx.exports.ContextBridge({props}),OBSERVER:ctx.exports.ContextObserver({props})},
        globalOutbound:null,limits:{cpuMs:1000,subRequests:8},tails:[ctx.exports.ContextTail({props})]});
      const response=await child.getEntrypoint().fetch('https://synthetic.invalid/');
      const body=await response.json();
      run.events.push('response_eof');
      const tailBeforeRelease=await boundedTail(run);
      const beforeRelease=[...run.events];
      if(mode!=='control') {run.events.push('release');run.release();}
      await new Promise(resolve=>setTimeout(resolve,300));
      return {body,tail_seen_before_release:tailBeforeRelease,before_release:beforeRelease,events:[...run.events],tail:run.tail};
    }
    try {
      const control=await exercise('control');
      const retained=await exercise('retained');
      const ended=await exercise('context_end');
      return Response.json({control,retained,ended,observation_window_ms:300,physical_termination_verified:false,cloud_invocations:0,
        scope:'Local request-owned Python child; bounded synthetic I/O context observation only'});
    } finally {
      for(const id of ids) {const run=runs.get(id);run.release();runs.delete(id);}
    }
  },
};
