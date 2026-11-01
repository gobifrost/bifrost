// Fixed synthetic local fixture. No arbitrary code, platform identity or admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {workerCode} from './isolation-code.mjs';
export class Effects extends WorkerEntrypoint {
  async record(event) {
    if(!['before','caught','after'].includes(String(event))) throw new TypeError('Invalid fixed event');
    const response=await fetch('http://127.0.0.1:8820/effect',{
      method:'POST',body:JSON.stringify({id:this.ctx.props.id,event:String(event)}),
      signal:AbortSignal.timeout(2000)});
    if(!response.ok) throw new Error('Independent observer refused event');
  }
}
export default {
  async fetch(request,env,ctx) {
    if(env.LOCAL_PROBE!=='true' || request.method!=='POST') return new Response('Local POST only',{status:403});
    const {id,mode}=await request.json();
    if(!/^[a-f0-9-]{36}$/.test(id) || !['control','abort','catch'].includes(mode)) {
      return new Response('Fixed fixture required',{status:422});
    }
    const worker=env.LOADER.load(workerCode('synthetic-termination-fixture',ctx.exports.Effects({props:{id}})));
    return worker.getEntrypoint().fetch(new Request('https://synthetic.invalid/',{
      method:'POST',body:JSON.stringify({action:mode==='catch' ? 'cpu_catch' : 'cpu'}),signal:request.signal}));
  },
};
