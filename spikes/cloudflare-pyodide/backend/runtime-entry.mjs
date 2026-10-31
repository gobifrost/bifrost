import {runtimeCode} from './runtime-code.mjs';
import {WorkerEntrypoint} from 'cloudflare:workers';
import {verifyModuleResponse} from './module-response.mjs';
const observations=new Map();

export class RejectedApi extends WorkerEntrypoint {
  async fetch(request) {
    const url=new URL(request.url);
    if(url.origin!=='https://local-api.invalid' || request.method!=='GET' ||
       url.pathname!=='/api/sdk/modules/workflows/unavailable-fixture.py') {
      return new Response('Not admitted',{status:403});
    }
    // No real credential, no caller-selected API, no mutation or auth refresh.
    const response=await fetch('http://host.docker.internal:39046'+url.pathname,{
      headers:{Authorization:'Bearer synthetic-invalid-runtime-token'},redirect:'manual',
    });
    observations.get(this.ctx.props.id)?.push(response.status);
    // This negative diagnostic has no approved workspace artifact. An unexpected
    // success must not execute code; a real API 401 passes through unchanged.
    return verifyModuleResponse(response,url.pathname.slice('/api/sdk/modules/'.length),undefined);
  }
}

export default {
  async fetch(request,env,ctx) {
    if(env.LOCAL_PROBE !== 'true') return new Response('Local inspection only',{status:403});
    // No credential-bearing execution endpoint until trusted control integration exists.
    if(request.method!=='POST') return new Response('POST required',{status:405});
    const body=await request.json();
    if(!['inspect','sync_transport','invalid_credential'].includes(body.action)) return new Response('Not admitted',{status:422});
    if(observations.size>=8) return new Response('Probe capacity exceeded',{status:429});
    const id=crypto.randomUUID();
    const statuses=[];
    observations.set(id,statuses);
    try {
      const outbound=body.action==='invalid_credential' ? ctx.exports.RejectedApi({props:{id}}) : null;
      const response=await env.LOADER.load(runtimeCode(outbound)).getEntrypoint().fetch(new Request('https://attempt.invalid/',{
        method:'POST',body:JSON.stringify(body),
      }));
      if(body.action==='invalid_credential') return Response.json({result:await response.json(),api_statuses:statuses});
      return response;
    } finally { observations.delete(id); }
  },
};
