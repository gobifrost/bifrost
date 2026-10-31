import {readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {cloudProbeFetch,boundedJson,boundedText,CLOUD_PROBE_URL} from './probe-io.mjs';
const {SPIKE_AUTH} = JSON.parse(await readFile(join(tmpdir(),'bifrost-pyodide-ddf316c1-secrets.json'),'utf8'));
process.env.SPIKE_AUTH=SPIKE_AUTH;
process.env.SPIKE_URL=CLOUD_PROBE_URL;
delete process.env.SPIKE_TOKEN; // Never inherit a local BiFrost identity into a cloud probe.
process.env.SPIKE_RUNTIME='deployed-cloudflare-python-worker';
process.env.SPIKE_OUTPUT='results/benchmark-cloud.json';
const unauth = await cloudProbeFetch();
const start=performance.now();
const response=await cloudProbeFetch(SPIKE_AUTH);
const body=await boundedJson(response);
await writeFile('results/cloud-imports.json',JSON.stringify({unauthenticated_status:unauth.status,
  authenticated_status:response.status,first_authenticated_client_wall_ms:performance.now()-start,body},null,2)+'\n');
if(response.status!==200 || unauth.status!==401) throw Error('remote auth or runtime failed');
await import('./bench.mjs');
// Memory probe is last: it may destroy the isolate and invalidate warm samples.
const memory=await cloudProbeFetch(SPIKE_AUTH,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({workload:'memory_probe'}),signal:AbortSignal.timeout(20000)});
const text=await boundedText(memory);
await writeFile('results/cloud-memory.json',JSON.stringify({status:memory.status,body:text.slice(0,3000)},null,2)+'\n');
console.log(`Cloud memory probe HTTP ${memory.status}`);
