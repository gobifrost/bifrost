// Local synthetic deadline experiment. No upstream claim or untrusted admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {createAttemptEgress} from './attempt-egress.mjs';
const attempts=new Map();
async function observer(id,action) {
  const response=await fetch(`http://127.0.0.1:8820/${action}?id=${id}`,{method:action==='snapshot'?'GET':'POST',signal:AbortSignal.timeout(2000)});
  if(!response.ok)throw new Error('Synthetic observer refused request');
  return response;
}
export class CpuEgress extends WorkerEntrypoint {
  async fetch(request) {
    const attempt=attempts.get(this.ctx.props.id);
    if(!attempt)return new Response('Expired',{status:410});
    const action=new URL(request.url).pathname;
    if(action==='/started'&&!attempt.gate) {
      // Fixed diagnostic activation, NOT a new canonical workflow deadline.
      attempt.started=Date.now();attempt.deadline=attempt.started+500;
      attempt.gate=createAttemptEgress(attempt.deadline,()=>observer(this.ctx.props.id,'after'));
      return observer(this.ctx.props.id,'started');
    }
    if(action!=='/after'||!attempt.gate)return new Response('Fixed path only',{status:403});
    attempt.effect_attempted=Date.now();
    const response=await attempt.gate.fetch(request);
    attempt.effect_status=response.status;
    return response;
  }
}
const python=`
from workers import WorkerEntrypoint
from pyodide.http import pyfetch
class Default(WorkerEntrypoint):
    async def run(self, mode):
        response = await pyfetch("https://synthetic.invalid/started", method="POST")
        if response.status != 200:
            raise RuntimeError("Synthetic start refused")
        await response.string()
        total = 0
        for value in range(20000000 if mode == "cpu" else 0):
            total += value
        response = await pyfetch("https://synthetic.invalid/after", method="POST")
        status = response.status
        await response.string()
        return {"total": total, "effect_status": status}
`;
export default {
  async fetch(request,env,ctx) {
    if(env.ISOLATED_TEST_STACK!=='true'||request.method!=='POST')return new Response('Local POST only',{status:403});
    const ids=[];
    async function exercise(mode) {
      const id=crypto.randomUUID();ids.push(id);
      await (await observer(id,'create')).text();
      const attempt={gate:null};attempts.set(id,attempt);
      const child=env.LOADER.load({compatibilityDate:'2026-10-30',compatibilityFlags:['python_workers'],mainModule:'main.py',
        modules:{'main.py':{py:python}},env:{},globalOutbound:ctx.exports.CpuEgress({props:{id}}),limits:{cpuMs:1000,subRequests:4}});
      const result=await child.getEntrypoint().run(mode);
      if(!(result instanceof Map))throw new TypeError('Pinned Python Map required');
      const body={total:result.get('total'),effect_status:result.get('effect_status')};result[Symbol.dispose]();
      return {body,observer:await (await observer(id,'snapshot')).json(),
        diagnostic_deadline_ms:500,effect_attempt_elapsed_ms:attempt.effect_attempted-attempt.started,
        effect_status:attempt.effect_status};
    }
    try {
      const control=await exercise('control'),cpu=await exercise('cpu');
      return Response.json({control,cpu,cloud_invocations:0,physical_termination_verified:false,
        scope:'Finite local CPU completion with parent capability deadline; no canonical timeout or provider stop proof'});
    } finally {for(const id of ids){attempts.get(id)?.gate?.close();attempts.delete(id);}}
  },
};
