import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {cloudProbeFetch,boundedJson} from './probe-io.mjs';
const {SPIKE_AUTH}=JSON.parse(await readFile(join(tmpdir(),'bifrost-pyodide-ddf316c1-secrets.json'),'utf8'));
async function invoke(name,parameters={},extra={}) {
  const response=await cloudProbeFetch(SPIKE_AUTH,{method:'POST',headers:{'Content-Type':'application/json',...extra},
    body:JSON.stringify({workload:name,parameters}),signal:AbortSignal.timeout(20000)},'/'+name);
  return {status:response.status,body:await boundedJson(response)};
}
const records=[];
for(const name of ['state_write','state_read','duplicate_probe','duplicate_probe']) records.push({name,...await invoke(name)});
assert.equal(records[1].body.result,'synthetic-execution-A');
assert.equal(records[3].body.result,records[2].body.result+1);
const oversized=await invoke('json_transform',{padding:'x'.repeat(70000)});
assert.equal(oversized.status,413);
const credential=await invoke('integration_read',{}, {'X-Spike-Token':'synthetic-invalid-token'});
assert.equal(credential.status,403);
await writeFile('results/security-cloud.json',JSON.stringify({records,oversized,credential_forwarding_block:credential},null,2)+'\n');
console.log('Cloud shared state, duplicate execution, payload rejection and credential-forwarding block asserted.');
