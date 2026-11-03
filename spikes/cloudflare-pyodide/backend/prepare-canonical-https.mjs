// Produce a separate fixed local entrypoint; never modify the SDK-only entry.
import {readFileSync, writeFileSync} from 'node:fs';
let source = readFileSync(new URL('authorized-local-entry.mjs', import.meta.url), 'utf8');
function replace(before, after) {
  if (source.split(before).length !== 2) throw new Error('Canonical fixture source changed');
  source = source.replace(before, after);
}
replace("const observations=new Map();", `import {createApprovedHttpsCandidate} from './approved-https-candidate.mjs';
import redirectPatch from './httpx-redirect-code.mjs';
import {boundHttpsFixturePolicy} from './fixture-https-policy.mjs';
const httpsPolicies=new Map();
const httpsExecutions=new Map();
const observations=new Map();`);
replace('const body=await boundedLosslessJson(request,262144);', `if(new URL(request.url).pathname==='/__fixture_revoke') {
      const control=await boundedLosslessJson(request,1024);
      const handle=control.runtime_handle;
      if(httpsExecutions.get(handle)!==control.execution_id || !httpsPolicies.has(handle) || !egressGates.has(handle))
        return new Response('Unknown bound attempt',{status:404});
      httpsPolicies.get(handle).close();egressGates.get(handle).close();
      return Response.json({execution_id:control.execution_id,runtime_handle:handle,
        capabilities_revoked:true,physical_stopped:false});
    }
    const body=await boundedLosslessJson(request,262144);`);
replace('observations.set(id,calls);', 'observations.set(id,calls);httpsExecutions.set(id,body.execution_id);');
replace('return admitFixture(body,observations.size,async()=>{', `const boundPolicy=boundHttpsFixturePolicy(body);
    if(!boundPolicy)return new Response('Bound HTTPS fixture policy required',{status:422});
    return admitFixture(body,observations.size,async()=>{`);
replace("return new Response('Test API capability only',{status:403});",
  "return httpsPolicies.get(this.ctx.props.id)?.fetch(request) ?? new Response('Closed HTTPS fixture',{status:410});");
replace('egressGates.set(id,egress);', `egressGates.set(id,egress);
    const https=createApprovedHttpsCandidate({origins:boundPolicy.origins,
      forbiddenCredentials:[body.context.engine_token,body.context.engine_refresh_token].filter(value=>typeof value==='string'&&value.length),
      maxRequests:boundPolicy.max_requests,deadline,forward:async outgoing=>{
        const path=new URL(outgoing.url).pathname;
        if(!['/write','/redirect','/hold'].includes(path))return new Response('Fixed path only',{status:403});
        return fetch('http://127.0.0.1:8820'+path,{method:outgoing.method,headers:outgoing.headers,
          body:outgoing.method==='GET'?undefined:await outgoing.text(),redirect:'manual'});
      }});
    httpsPolicies.set(id,https);`);
replace('cleaned=true;', 'cleaned=true;https.close();httpsPolicies.delete(id);httpsExecutions.delete(id);');
replace(`const entrypoint=env.LOADER.load(runtimeCode(ctx.exports.TestApi({props:{id,manifest:body.manifest,refresh_gate:!!gate}})))
        .getEntrypoint();`, `const code=runtimeCode(ctx.exports.TestApi({props:{id,manifest:body.manifest,refresh_gate:!!gate}}));
      code.modules['httpx_redirect_patch.py']={py:redirectPatch};
      code.modules['main.py'].py=${JSON.stringify('from httpx_redirect_patch import install\ninstall()\n')}+code.modules['main.py'].py;
      code.limits.subRequests=32;
      const entrypoint=env.LOADER.load(code).getEntrypoint();`);
if (process.argv.length !== 3) throw new Error('Usage: node prepare-canonical-https.mjs <new-output-file>');
writeFileSync(process.argv[2], source, {flag: 'wx'});
