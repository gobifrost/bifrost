// One-use CT128 test provider. Not a production sandbox or authoritative queue.
import {loadPyodide} from '/core/pyodide.mjs';
import {createServer} from 'node:http';
import {boundedJson,diagnosticJson} from '/spike/probe-io.mjs';
import {fixtureRejection} from '/spike/backend/fixture-admission.mjs';
import {testApiRouteAllowed} from '/spike/backend/test-api-route.mjs';
import {verifyModuleResponse} from '/spike/backend/module-response.mjs';
import {verifyResolutionResponse} from '/spike/backend/resolution-response.mjs';
const startedBootstrap=performance.now();
const py=await loadPyodide();
const memorySamples=[];
function snapshotMemory(phase) {
  const proxy=py.runPython('memoryview(bytearray(1))');
  let view;
  try {
    view=proxy.getBuffer('u8');
    const bytes=view.data.buffer.byteLength;
    if(!Number.isSafeInteger(bytes) || bytes<=0 || bytes%65536!==0) throw new Error('Invalid WASM memory view');
    memorySamples.push({phase,wasm_linear_memory_capacity_bytes:bytes,node_process:process.memoryUsage()});
  } finally {view?.release();proxy.destroy();}
}
snapshotMemory('core_loaded');
for(const [name,root] of [['packages','/spike/python_modules'],['source','/spike/backend/runtime-source'],['backend','/spike/backend']]) {
  py.FS.mkdir('/'+name);py.FS.mount(py.FS.filesystems.NODEFS,{root},'/'+name);
}
try {
await py.runPythonAsync(`
import sys
sys.path[:0]=['/source','/packages','/backend']
import importlib.util
_spec=importlib.util.spec_from_file_location('runtime_runner','/backend/runtime-runner.py')
_runner=importlib.util.module_from_spec(_spec)
sys.modules['runtime_runner']=_runner
_spec.loader.exec_module(_runner)
run_authorized_attempt=_runner.run_authorized_attempt
from src.services.execution.result_envelope import execution_result_envelope
`);
} catch(error) {
  // Startup precedes admission of any credential-bearing request.
  console.error(String(error.message).slice(-8000));
  process.exit(1);
}
snapshotMemory('upstream_bootstrap_complete');
const bootstrapWallMs=performance.now()-startedBootstrap;
const originalFetch=globalThis.fetch;
let manifest;
const calls=[];
globalThis.fetch=async(input,init)=>{
  const request=new Request(input,init);
  const url=new URL(request.url);
  if(url.origin!=='http://api:8000' || !testApiRouteAllowed(new URL('https://test-api.invalid'+url.pathname+url.search))) {
    throw new Error('Fixed test API capability only');
  }
  const response=await originalFetch(request,{redirect:'error'});
  calls.push({method:request.method,path:url.pathname,status:response.status});
  if(url.pathname.startsWith('/api/sdk/modules/')) {
    const path=decodeURIComponent(url.pathname.slice('/api/sdk/modules/'.length));
    return verifyModuleResponse(response,path,manifest?.[path]);
  }
  if(url.pathname==='/api/sdk/modules-resolve') return verifyResolutionResponse(response,manifest ?? {});
  return response;
};
let used=false;
createServer(async(request,response)=>{
  if(request.method==='GET' && request.url==='/health') return response.end('ready');
  if(request.method!=='POST' || request.url!=='/') return response.writeHead(404).end();
  try {
    const body=await boundedJson(new Response(request),262144);
    const reason=fixtureRejection(body,used ? 4 : 0);
    if(reason) return response.writeHead(422).end(JSON.stringify({error:'Fixture not admitted',reason}));
    if(body.mode!=='canonical' || body.fixture_mode!==undefined) return response.writeHead(422).end();
    used=true;
    manifest=body.manifest;
    py.globals.set('_fixed_request_json',JSON.stringify(body));
    const started=performance.now();
    const result=await py.runPythonAsync(`
import json,time
_request=json.loads(_fixed_request_json)
_started=time.perf_counter()
_result=await run_authorized_attempt(_request['execution_id'],_request['context'],'http://api:8000')
_envelope=execution_result_envelope(_request['execution_id'],_request['worker_id'],_result,
    duration_ms=int((time.perf_counter()-_started)*1000),metrics=_result.get('metrics') or {})
json.dumps(_envelope)
`);
    snapshotMemory('sdk_execution_complete');
    const evidence={runtime:'official-pyodide-on-node',memory_samples:memorySamples,bootstrap_wall_ms:bootstrapWallMs,result:JSON.parse(result),api_calls:calls,
      provider_wall_ms:performance.now()-started,cloud_invocations:0,physical_termination_verified:false};
    const encoded=diagnosticJson(evidence,[body.context.engine_token,body.context.engine_refresh_token ?? '']);
    response.writeHead(200,{'Content-Type':'application/json'}).end(encoded);
  } catch(error) {
    // Do not persist request/credential material or untrusted exception messages.
    const stack=String(error.stack);
    console.error(JSON.stringify({error_type:error.name,
      missing_module:/ModuleNotFoundError: No module named ['"]([a-zA-Z_][a-zA-Z0-9_.]*)/.exec(stack)?.[1],
      locations:[...stack.matchAll(/File "(\/source\/[^"\r\n]+)", line (\d+)/g)].map(m=>({file:m[1],line:Number(m[2])})),
      api_calls:calls}));
    response.writeHead(500,{'Content-Type':'application/json'}).end(JSON.stringify({error_type:error.name}));
  }
}).listen(8799,'0.0.0.0',()=>console.log('Fixed Node Pyodide provider ready'));
