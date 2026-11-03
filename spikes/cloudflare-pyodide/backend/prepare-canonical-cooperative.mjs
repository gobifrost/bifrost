// Compose a separate local fixture. Never deploy or enable public admission.
import {readFileSync, writeFileSync} from 'node:fs';

if (process.argv.length !== 4) throw new Error('Usage: node prepare-canonical-cooperative.mjs <https-entry> <new-output>');
let source = readFileSync(process.argv[2], 'utf8');
function replace(before, after) {
  if (source.split(before).length !== 2) throw new Error('Canonical HTTPS fixture source changed');
  source = source.replace(before, after);
}
replace('const httpsExecutions=new Map();', `import {withCooperativeRpc} from './cooperative-runtime.mjs';
import cooperativeSource from './cooperative-code.mjs';
const cooperativeEntries=new Map();
const httpsExecutions=new Map();`);
replace("if(new URL(request.url).pathname==='/__fixture_revoke') {", `if(new URL(request.url).pathname==='/__fixture_cancel') {
      const control=await boundedLosslessJson(request,1024);
      const handle=control.runtime_handle;
      if(httpsExecutions.get(handle)!==control.execution_id || !cooperativeEntries.has(handle))
        return new Response('Unknown bound attempt',{status:404});
      httpsPolicies.get(handle).close();egressGates.get(handle).close();
      const requested=await cooperativeEntries.get(handle).cancel_execution(control.execution_id);
      return Response.json({execution_id:control.execution_id,runtime_handle:handle,
        cancellation_requested:requested,physical_stopped:false,execution_settled:false});
    }
    if(new URL(request.url).pathname==='/__fixture_revoke') {`);
replace('cleaned=true;https.close();', 'cleaned=true;cooperativeEntries.delete(id);https.close();');
replace('const entrypoint=env.LOADER.load(code).getEntrypoint();',
  'const entrypoint=env.LOADER.load(withCooperativeRpc(code,cooperativeSource)).getEntrypoint();\n      cooperativeEntries.set(id,entrypoint);');
replace(`const response=await entrypoint.fetch(new Request('https://attempt.invalid/',{
          method:'POST',body:JSON.stringify({action:body.mode==='canonical' ? 'execute_envelope' : 'execute',
            worker_id:body.worker_id,execution_id:body.execution_id,
            context:body.context,api_url:'https://test-api.invalid'}),
        }));`, `const rpc=JSON.parse(await entrypoint.run_execution(JSON.stringify({
          action:body.mode==='canonical' ? 'execute_envelope' : 'execute',
          worker_id:body.worker_id,execution_id:body.execution_id,
          context:body.context,api_url:'https://test-api.invalid'})));
      // A coroutine receipt is not a canonical terminal result. Keep the
      // transport uncertain rather than fabricating Cancelled or releasing capacity.
      if(rpc.outcome?.kind!=='engine_result')throw new Error('Cooperative coroutine settled; canonical execution remains uncertain');
      const response=Response.json(rpc.outcome.body,{headers:{
        'X-Spike-SDK-Close-Returned':rpc.sdk_close_returned===true ? 'true' : 'false'}});`);
writeFileSync(process.argv[3], source, {flag: 'wx'});
