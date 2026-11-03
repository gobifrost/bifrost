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
      return new Response('Test API capability only',{status:403});
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
    const gate=body.fixture_mode==='refresh_interruption' ? createRefreshGate() : undefined;
    if(gate) refreshGates.set(id,gate);
    let cleaned=false;
    const cleanup=()=>{
      if(cleaned) return;
      cleaned=true;
      egress.close();egressGates.delete(id);gate?.close();refreshGates.delete(id);observations.delete(id);
    };
    try {
      const entrypoint=env.LOADER.load(runtimeCode(ctx.exports.TestApi({props:{id,manifest:body.manifest,refresh_gate:!!gate}})))
        .getEntrypoint();
      const execute=async()=>{
      const response=await entrypoint.fetch(new Request('https://attempt.invalid/',{
          method:'POST',body:JSON.stringify({action:body.mode==='canonical' ? 'execute_envelope' : 'execute',
            worker_id:body.worker_id,execution_id:body.execution_id,
            context:body.context,api_url:'https://test-api.invalid'}),
        }));
      const terminal=await terminalResponse(response);
      if(terminal.oversized) return {runtime_handle:id,
        result:{type:'result',execution_id:body.execution_id,success:false,
          error_type:'ExecutorBodyTooLargeError',
          error:'Executor response exceeds retention budget; workflow effects may already exist'},
        provider_outcome:{kind:'terminal_body_too_large',response_eof:true,
          engine_outcome_delivered:false,response_bytes:terminal.response_bytes,
          retention_limit_bytes:1048576,drain_limit_bytes:4194304}};
      return {runtime_handle:id,result:terminal.value,api_calls:calls,
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
