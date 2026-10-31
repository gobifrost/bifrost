// Local-only diagnostic. Effects are observed by a separate Node event loop.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {workerCode} from './isolation-code.mjs';
export class Effects extends WorkerEntrypoint {
  async record(event) {
    if(!['before','after'].includes(String(event))) throw new TypeError('Invalid event');
    const response=await fetch('http://127.0.0.1:8800/effect',{
      method:'POST',body:String(event),
    });
    if(!response.ok) throw new Error('External observer unavailable');
  }
}
export default {
  async fetch(request,env,ctx) {
    if(env.LOCAL_PROBE!=='true') return new Response('Local only',{status:403});
    if(request.method!=='POST') return new Response('POST required',{status:405});
    // Fixed CPU fixture only; no credentials or arbitrary workflow admission.
    const worker=env.LOADER.load(workerCode('external-abort-canary',ctx.exports.Effects({props:{}})));
    return worker.getEntrypoint().fetch(new Request('https://attempt.invalid/',{
      method:'POST',body:JSON.stringify({action:'cpu'}),signal:request.signal,
    }));
  },
};
