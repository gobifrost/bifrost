// Test-stack qualification only; not a public or authoritative dispatch API.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {runtimeCode} from './runtime-code.mjs';
import {boundedJson} from '../probe-io.mjs';
import {verifyModuleResponse} from './module-response.mjs';
import {verifyResolutionResponse} from './resolution-response.mjs';
const observations=new Map();
export class TestApi extends WorkerEntrypoint {
  async fetch(request) {
    const url=new URL(request.url);
    if(url.origin!=='https://test-api.invalid' ||
       !(url.pathname.startsWith('/api/sdk/') || url.pathname==='/api/tables' || url.pathname.startsWith('/api/tables/') ||
         url.pathname==='/api/auth/refresh')) {
      return new Response('Test API capability only',{status:403});
    }
    const response=await fetch(new Request('http://api:8000'+url.pathname+url.search,request),{redirect:'manual'});
    observations.get(this.ctx.props.id)?.push({method:request.method,path:url.pathname,status:response.status});
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
    const body=await boundedJson(request,262144);
    if(!/^workflows\/pyodide_qualification_[a-f0-9]{32}\.py$/.test(body.context?.file_path ?? '') ||
       body.context?.function_name!=='qualified' || !body.context?.engine_token ||
       body.context?.code || body.context?.service || body.context?.is_platform_admin ||
       (body.mode!==undefined && body.mode!=='canonical') ||
       (body.mode==='canonical' && !/^[a-f0-9-]{36}$/.test(body.worker_id ?? '')) ||
       !body.manifest?.[body.context.file_path] || observations.size>=4) {
      return new Response('Fixture not admitted',{status:422});
    }
    const id=crypto.randomUUID();
    const calls=[];
    observations.set(id,calls);
    try {
      const response=await env.LOADER.load(runtimeCode(ctx.exports.TestApi({props:{id,manifest:body.manifest}})))
        .getEntrypoint().fetch(new Request('https://attempt.invalid/',{
          method:'POST',body:JSON.stringify({action:body.mode==='canonical' ? 'execute_envelope' : 'execute',
            worker_id:body.worker_id,execution_id:body.execution_id,
            context:body.context,api_url:'https://test-api.invalid'}),
        }));
      return Response.json({result:await boundedJson(response),api_calls:calls});
    } finally { observations.delete(id); }
  },
};
