// Compose a separate local fixture. Never deploy or enable public admission.
import {readFileSync, writeFileSync} from 'node:fs';

if (process.argv.length !== 4) throw new Error('Usage: node prepare-canonical-cooperative.mjs <https-entry> <new-output>');
let source = readFileSync(process.argv[2], 'utf8');
function replace(before, after) {
  if (source.split(before).length !== 2) throw new Error('Canonical HTTPS fixture source changed');
  source = source.replace(before, after);
}
replace('const httpsExecutions=new Map();', `import {withCooperativeRpc} from './cooperative-runtime.mjs';
import {cooperativeRpcResponse} from './cooperative-rpc-result.mjs';
import cooperativeSource from './cooperative-code.mjs';
import serializationSource from './rpc-serialization-code.mjs';
const cooperativeEntries=new Map();
const httpsExecutions=new Map();`);
replace("if(new URL(request.url).pathname==='/__fixture_revoke') {", `if(new URL(request.url).pathname==='/__fixture_cancel') {
      const control=await boundedLosslessJson(request,1024);
      const handle=control.runtime_handle;
      if(httpsExecutions.get(handle)!==control.execution_id || !cooperativeEntries.has(handle))
        return new Response('Unknown bound attempt',{status:404});
      httpsPolicies.get(handle).close();egressGates.get(handle).close();
      cooperativeEntries.get(handle).requested=true;
      return Response.json({execution_id:control.execution_id,runtime_handle:handle,
        cancellation_requested:true,physical_stopped:false,execution_settled:false});
    }
    if(new URL(request.url).pathname==='/__fixture_revoke') {`);
replace('cleaned=true;https.close();', 'cleaned=true;cooperativeEntries.delete(id);https.close();');
replace('const entrypoint=env.LOADER.load(code).getEntrypoint();',
  'const entrypoint=env.LOADER.load(withCooperativeRpc(code,cooperativeSource,serializationSource)).getEntrypoint();\n      const cooperativeControl={requested:false};cooperativeEntries.set(id,cooperativeControl);');
replace(`const response=await entrypoint.fetch(new Request('https://attempt.invalid/',{
          method:'POST',body:JSON.stringify({action:body.mode==='canonical' ? 'execute_envelope' : 'execute',
            worker_id:body.worker_id,execution_id:body.execution_id,
            context:body.context,api_url:'https://test-api.invalid'}),
        }));`, `const pending=entrypoint.run_execution(JSON.stringify({
          action:body.mode==='canonical' ? 'execute_envelope' : 'execute',
          worker_id:body.worker_id,execution_id:body.execution_id,
          context:body.context,api_url:'https://test-api.invalid'}));
      let monitorRunning=true;
      const monitor=(async()=>{
        while(monitorRunning){
          await new Promise(resolve=>setTimeout(resolve,10));
          if(cooperativeControl.requested){
            await entrypoint.cancel_execution(body.execution_id);
            return;
          }
        }
      })();
      let rpcWire;
      try{rpcWire=await Promise.race([pending,monitor.then(()=>pending)]);}
      finally{monitorRunning=false;await monitor;}
      // A coroutine receipt is not a canonical terminal result. Keep the
      // transport uncertain rather than fabricating Cancelled or releasing capacity.
      const response=cooperativeRpcResponse(rpcWire);`);
writeFileSync(process.argv[3], source, {flag: 'wx'});
