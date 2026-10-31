import {readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const {SPIKE_AUTH} = JSON.parse(await readFile(join(tmpdir(),'bifrost-pyodide-ddf316c1-secrets.json'),'utf8'));
process.env.SPIKE_AUTH=SPIKE_AUTH;
process.env.SPIKE_URL='https://bifrost-pyodide-spike-ddf316c1.midtown-technology-group.workers.dev';
process.env.SPIKE_RUNTIME='deployed-cloudflare-python-worker';
process.env.SPIKE_OUTPUT='results/benchmark-cloud.json';
const unauth = await fetch(process.env.SPIKE_URL);
const start=performance.now();
const response=await fetch(process.env.SPIKE_URL,{headers:{'X-Spike-Auth':SPIKE_AUTH}});
const body=await response.json();
await writeFile('results/cloud-imports.json',JSON.stringify({unauthenticated_status:unauth.status,
  authenticated_status:response.status,first_authenticated_client_wall_ms:performance.now()-start,body},null,2)+'\n');
if(response.status!==200 || unauth.status!==401) throw Error('remote auth or runtime failed');
await import('./bench.mjs');
// Memory probe is last: it may destroy the isolate and invalidate warm samples.
const memory=await fetch(process.env.SPIKE_URL,{method:'POST',headers:{'Content-Type':'application/json','X-Spike-Auth':SPIKE_AUTH},
  body:JSON.stringify({workload:'memory_probe'}),signal:AbortSignal.timeout(20000)});
const text=await memory.text();
await writeFile('results/cloud-memory.json',JSON.stringify({status:memory.status,body:text.slice(0,3000)},null,2)+'\n');
console.log(`Cloud memory probe HTTP ${memory.status}`);
