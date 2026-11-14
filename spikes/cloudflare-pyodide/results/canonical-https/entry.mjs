// Test-stack qualification only; not a public or authoritative dispatch API.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {runtimeCode} from './runtime-code.mjs';
import {boundedJson} from '../probe-io.mjs';
import {boundedLosslessJson} from './lossless-json.mjs';
import {terminalResponse} from './terminal-response.mjs';
import {verifyModuleResponse} from './module-response.mjs';
import {verifyResolutionResponse} from './resolution-response.mjs';
import {admitFixture} from './fixture-admission.mjs';
import {testApiRouteAllowed} from './test-api-route.mjs';
import {createRefreshGate} from './refresh-gate.mjs';
import {createAttemptEgress} from './attempt-egress.mjs';
import {invocationStream} from './invocation-stream.mjs';
import {sourceIntegrityResponse} from './source-integrity-response.mjs';
import {createApprovedHttpsCandidate} from './approved-https-candidate.mjs';
import redirectPatch from './httpx-redirect-code.mjs';
const httpsPolicies=new Map();
const observations=new Map();
const refreshGates=new Map();
const egressGates=new Map();
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
      return httpsPolicies.get(this.ctx.props.id)?.fetch(request) ?? new Response('Closed HTTPS fixture',{status:410});
    }
    const egress=egressGates.get(this.ctx.props.id);
    if(!egress) return new Response('Attempt capability closed',{status:410});
    const response=await egress.fetch(new Request('http://api:8000'+url.pathname+url.search,request));
    const observation={method:request.method,path:url.pathname,status:response.status};
    observations.get(this.ctx.props.id)?.push(observation);
    if(gate && url.pathname==='/auth/refresh' && response.status===200) await gate.holdResponse();
    if(url.pathname.startsWith('/api/sdk/modules/')) {
      const path=decodeURIComponent(url.pathname.slice('/api/sdk/modules/'.length));
      return sourceIntegrityResponse(()=>verifyModuleResponse(response,path,this.ctx.props.manifest[path]),observation);
    }
    if(url.pathname==='/api/sdk/modules-resolve') {
      return sourceIntegrityResponse(()=>verifyResolutionResponse(response,this.ctx.props.manifest),observation);
    }
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
    const body=await boundedLosslessJson(request,262144);
    return admitFixture(body,observations.size,async()=>{
    const id=crypto.randomUUID();
    const calls=[];
    observations.set(id,calls);
    // Canonical mode consumes the dispatcher's cutoff, never a new window on
    // receipt. Direct API fixtures retain their separate local duration cap.
    // The fixed local caller is not signed-control/provider admission proof.
    const deadline=body.mode==='canonical' ? Date.parse(body.context.workflow_deadline)
      : Date.now()+body.context.timeout_seconds*1000;
    const egress=createAttemptEgress(deadline,
      outgoing=>fetch(outgoing,{redirect:'manual'}));
    egressGates.set(id,egress);
    const https=createApprovedHttpsCandidate({origins:['https://vendor.example'],
      forbiddenCredentials:[body.context.engine_token,body.context.engine_refresh_token].filter(value=>typeof value==='string'&&value.length),
      maxRequests:6,deadline,forward:async outgoing=>{
        const path=new URL(outgoing.url).pathname;
        if(!['/write','/redirect'].includes(path))return new Response('Fixed path only',{status:403});
        return fetch('http://127.0.0.1:8820'+path,{method:outgoing.method,headers:outgoing.headers,
          body:outgoing.method==='GET'?undefined:await outgoing.text(),redirect:'manual'});
      }});
    httpsPolicies.set(id,https);
    const gate=body.fixture_mode==='refresh_interruption' ? createRefreshGate() : undefined;
    if(gate) refreshGates.set(id,gate);
    let cleaned=false;
    const cleanup=()=>{
      if(cleaned) return;
      cleaned=true;https.close();httpsPolicies.delete(id);
      egress.close();egressGates.delete(id);gate?.close();refreshGates.delete(id);observations.delete(id);
    };
    try {
      const code=runtimeCode(ctx.exports.TestApi({props:{id,manifest:body.manifest,refresh_gate:!!gate}}));
      code.modules['httpx_redirect_patch.py']={py:redirectPatch};
      code.modules['main.py'].py="from httpx_redirect_patch import install\ninstall()\n"+code.modules['main.py'].py;
      code.limits.subRequests=32;
      const entrypoint=env.LOADER.load(code).getEntrypoint();
      const execute=async()=>{
      const response=await entrypoint.fetch(new Request('https://attempt.invalid/',{
          method:'POST',body:JSON.stringify({action:body.mode==='canonical' ? 'execute_envelope' : 'execute',
            worker_id:body.worker_id,execution_id:body.execution_id,
            context:body.context,api_url:'https://test-api.invalid'}),
        }));
      const runtime_cleanup={sdk_close_returned:response.headers.get('X-Spike-SDK-Close-Returned')==='true'};
      const terminal=await terminalResponse(response);
      if(terminal.oversized) return {runtime_handle:id,
        runtime_cleanup,
        result:{type:'result',execution_id:body.execution_id,success:false,
          error_type:'ExecutorBodyTooLargeError',
          error:'Executor response exceeds retention budget; workflow effects may already exist'},
        provider_outcome:{kind:'terminal_body_too_large',response_eof:true,
          engine_outcome_delivered:false,response_bytes:terminal.response_bytes,
          retention_limit_bytes:1048576,drain_limit_bytes:4194304}};
      return {runtime_handle:id,result:terminal.value,api_calls:calls,runtime_cleanup,
        ...(gate ? {refresh_gate_events:gate.events} : {})};
      };
      if(request.headers.get('Accept')==='application/x-ndjson') {
        // The response owns awaited work. Stream cancellation closes future
        // capability use; it does not certify physical Python termination.
        return invocationStream({runtimeHandle:id,executionId:body.execution_id,execute,cleanup});
      }
      try { return Response.json(await execute()); }
      finally { cleanup(); }
    } catch(error) { cleanup();throw error; }
    });
  },
};
