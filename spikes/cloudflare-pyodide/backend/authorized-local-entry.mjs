// Test-stack qualification only; not a public or authoritative dispatch API.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {runtimeCode} from './runtime-code.mjs';
import {boundedJson} from '../probe-io.mjs';
import {verifyModuleResponse} from './module-response.mjs';
import {verifyResolutionResponse} from './resolution-response.mjs';
import {admitFixture} from './fixture-admission.mjs';
import {testApiRouteAllowed} from './test-api-route.mjs';
import {createRefreshGate} from './refresh-gate.mjs';
const observations=new Map();
const refreshGates=new Map();
export class TestApi extends WorkerEntrypoint {
  async fetch(request) {
    const url=new URL(request.url);
    const gate=this.ctx.props.refresh_gate ? refreshGates.get(this.ctx.props.id) : undefined;
    if(gate && url.origin==='https://test-api.invalid') {
      if(url.pathname==='/__fixture_refresh/ready' && request.method==='GET') {
        const ready=await gate.waitReady();
        return Response.json({server_response_received:ready},{status:ready ? 200 : 409});
      }
      if(url.pathname==='/__fixture_refresh/release' && request.method==='POST') {
        gate.release();return Response.json({released:true});
      }
    }
    if(!testApiRouteAllowed(url)) {
      return new Response('Test API capability only',{status:403});
    }
    const response=await fetch(new Request('http://api:8000'+url.pathname+url.search,request),{redirect:'manual'});
    observations.get(this.ctx.props.id)?.push({method:request.method,path:url.pathname,status:response.status});
    if(gate && url.pathname==='/auth/refresh' && response.status===200) await gate.holdResponse();
    if(url.pathname.startsWith('/api/sdk/modules/')) {
      const path=decodeURIComponent(url.pathname.slice('/api/sdk/modules/'.length));
      return verifyModuleResponse(response,path,this.ctx.props.manifest[path]);
    }
    if(url.pathname==='/api/sdk/modules-resolve') return verifyResolutionResponse(response,this.ctx.props.manifest);
    return response;
  }
}
export default {
  async fetch(request,env,ctx) {
    if(env.ISOLATED_TEST_STACK!=='true') return new Response('Test stack only',{status:403});
    if(request.method!=='POST') return new Response('POST required',{status:405});
    if(new URL(request.url).pathname==='/__fixture_warmup') {
      // Fixed, credential-free Python startup probe. No workspace code or API
      // capability is passed; this is not production admission or a retry.
      const response=await env.LOADER.load(runtimeCode(null)).getEntrypoint().fetch(
        new Request('https://warmup.invalid/',{method:'POST',body:JSON.stringify({action:'inspect'})}));
      return Response.json(await boundedJson(response));
    }
    const body=await boundedJson(request,262144);
    return admitFixture(body,observations.size,async()=>{
    const id=crypto.randomUUID();
    const calls=[];
    observations.set(id,calls);
    const gate=body.fixture_mode==='refresh_interruption' ? createRefreshGate() : undefined;
    if(gate) refreshGates.set(id,gate);
    try {
      const response=await env.LOADER.load(runtimeCode(ctx.exports.TestApi({props:{id,manifest:body.manifest,refresh_gate:!!gate}})))
        .getEntrypoint().fetch(new Request('https://attempt.invalid/',{
          method:'POST',body:JSON.stringify({action:body.mode==='canonical' ? 'execute_envelope' : 'execute',
            worker_id:body.worker_id,execution_id:body.execution_id,
            context:body.context,api_url:'https://test-api.invalid'}),
        }));
      return Response.json({result:await boundedJson(response),api_calls:calls,
        ...(gate ? {refresh_gate_events:gate.events} : {})});
    } finally { gate?.close();refreshGates.delete(id);observations.delete(id); }
    });
  },
};
