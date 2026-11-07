// Fixed local HTTP cancellation experiment. Never execution admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {summarizeTail} from './tail-observer.mjs';
const tails=new Map();
async function observer(id,action) {
  const response=await fetch(`http://127.0.0.1:8820/${action}?id=${id}`,{method:action==='snapshot'?'GET':'POST',signal:AbortSignal.timeout(10000)});
  if(!response.ok) throw new Error('Synthetic HTTP observer refused request');
  return response;
}
export class HttpEgress extends WorkerEntrypoint {
  async fetch(request) {
    const action=new URL(request.url).pathname.slice(1);
    if(!['started','gate','after'].includes(action)) return new Response('Fixed path only',{status:403});
    return observer(this.ctx.props.id,action);
  }
}
export class HttpContextTail extends WorkerEntrypoint {
  tail(events) {
    const run=tails.get(this.ctx.props.id);
    if(!run) throw new Error('Synthetic tail expired');
    run.summary=summarizeTail(events);run.resolve();
  }
}
const python=`
from workers import WorkerEntrypoint
from pyodide.http import pyfetch
class Default(WorkerEntrypoint):
    async def held(self):
        for action in ("started", "gate", "after"):
            response = await pyfetch("https://synthetic.invalid/" + action, method="POST")
            if response.status != 200:
                raise RuntimeError("Synthetic HTTP refused")
            await response.string()
        return 7
`;
export class HttpContextBridge extends WorkerEntrypoint {
  async run() {
    const child=this.env.LOADER.load({compatibilityDate:'2026-10-30',compatibilityFlags:['python_workers'],
      mainModule:'main.py',modules:{'main.py':{py:python}},env:{},
      globalOutbound:this.ctx.exports.HttpEgress({props:this.ctx.props}),limits:{cpuMs:1000,subRequests:8}});
    return await child.getEntrypoint().held();
  }
}
export class HttpStarted extends WorkerEntrypoint {
  async wait() {await (await observer(this.ctx.props.id,'wait-gate')).text();return true;}
}
const caller=`export default {async fetch(request,env,ctx) {
  if(env.MODE==='control') return Response.json({value:await env.BRIDGE.run()});
  const pending=env.BRIDGE.run();pending.catch(()=>{});
  if(env.MODE==='retained') ctx.waitUntil(pending);
  await env.STARTED.wait();return Response.json({started:true});
}};`;
export default {
  async fetch(request,env,ctx) {
    if(env.ISOLATED_TEST_STACK!=='true'||request.method!=='POST') return new Response('Local POST only',{status:403});
    const ids=[];
    async function exercise(mode) {
      const id=crypto.randomUUID();ids.push(id);
      await (await observer(id,'create')).text();
      if(mode==='control') await (await observer(id,'release')).text();
      const run={summary:null};run.ready=new Promise(resolve=>{run.resolve=resolve;});tails.set(id,run);
      const props={id};
      const child=env.LOADER.load({compatibilityDate:'2026-10-30',mainModule:'main.js',modules:{'main.js':{js:caller}},
        env:{MODE:mode,BRIDGE:ctx.exports.HttpContextBridge({props}),STARTED:ctx.exports.HttpStarted({props})},
        globalOutbound:null,limits:{cpuMs:1000,subRequests:8},tails:[ctx.exports.HttpContextTail({props})]});
      const response=await child.getEntrypoint().fetch('https://synthetic.invalid/');
      const body=await response.json();
      await (await observer(id,'eof')).text();
      let timer;
      const tailBeforeRelease=await Promise.race([run.ready.then(()=>true),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),2500);})]);
      clearTimeout(timer);
      const before=await (await observer(id,'snapshot')).json();
      if(mode!=='control') await (await observer(id,'release')).text();
      await new Promise(resolve=>setTimeout(resolve,500));
      return {body,tail_seen_before_release:tailBeforeRelease,tail:run.summary,before_release:before,
        after:await (await observer(id,'snapshot')).json()};
    }
    try {
      const control=await exercise('control'),retained=await exercise('retained'),ended=await exercise('context_end');
      return Response.json({control,retained,ended,observation_window_ms:500,cloud_invocations:0,physical_termination_verified:false});
    } finally {
      for(const id of ids) {await (await observer(id,'release')).text();tails.delete(id);}
    }
  },
};
