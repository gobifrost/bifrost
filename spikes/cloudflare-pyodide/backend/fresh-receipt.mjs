import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root=new URL('../',import.meta.url);
const read=async name=>JSON.parse((await readFile(new URL('results/'+name+'.json',root),'utf8')).replace(/^\uFEFF/,''));
const names=['isolation-cloud-fresh','engine-cloud-fresh-bootstrap-failure','engine-cloud-diagnostic',
  'engine-cloud-fresh','cpu-limit-cloud-fresh','cpu-catch-cloud-fresh'];
const observations=new Map();
const versions=[];
for(const name of names) {
  const artifact=await read(name);
  versions.push({artifact:name+'.json',version:artifact.versions});
  for(const value of artifact.results) {
    if(observations.has(value.ordinal)) throw new Error('Duplicate observation ordinal');
    observations.set(value.ordinal,value);
  }
}
const ledger=await read('fresh-cloud-budget');
if(observations.size!==ledger.reservations.length || observations.size>20) throw new Error('Budget/receipt mismatch');
const hashes={};
for(const path of ['backend/isolation-code.mjs','backend/isolation-entry.mjs','backend/runtime-code.mjs',
  'backend/cloud-engine-entry.mjs','backend/cloud-isolation.mjs','cloudflare.config.ts',
  'results/dynamic-runtime-manifest.json']) {
  hashes[path]=createHash('sha256').update(await readFile(new URL(path,root))).digest('hex');
}
const clientWallMs=[...observations.values()].reduce((sum,value)=>sum+value.client_wall_ms,0);
const creation=observations.size*0.002;
const result={worker:'bifrost-pyodide-spike-ddf316c1',cli:'cf 1.0.0-beta.22',
  approved_max_invocations:20,approved_incremental_usd_cap:0.10,reserved_invocations:observations.size,
  bifrost_credentials_uploaded:false,production_resources_changed:false,versions,
  final_private_upload_version:'1c4abba7-4fc8-4e37-b5d9-99aef8c68b13',
  current_local_source_sha256:hashes,measured_total_client_wall_ms:clientWallMs,
  estimated_creation_usd_before_included_allocation:creation,
  conservative_cost_proxy_usd:creation+2*clientWallMs*0.02/1000000+6*observations.size*0.30/1000000,
  cost_note:'Estimate only, not invoice. Proxy counts two worker CPU durations at full client wall time plus six requests per reservation; actual CPU and billable allocation were not measured.',
  physical_stop_acceptance:false,first_class_executor_acceptance:false};
await writeFile(new URL('results/fresh-cloud-receipt.json',root),JSON.stringify(result,null,2)+'\n');
console.log({reserved:observations.size,client_wall_ms:clientWallMs,cost_proxy_usd:result.conservative_cost_proxy_usd});
