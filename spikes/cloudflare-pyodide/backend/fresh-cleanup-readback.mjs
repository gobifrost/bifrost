import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {cloudProbeFetch,CLOUD_PROBE_URL,diagnosticJson} from '../probe-io.mjs';
const read=async name=>JSON.parse((await readFile(new URL('../results/'+name,import.meta.url),'utf8')).replace(/^\uFEFF/,''));
const worker=await read('fresh-final-worker.json');
const secrets=await read('fresh-final-secrets.json');
// Parse historical captures without rewriting their original bytes.
assert.equal(worker.subdomain.enabled,false);
assert.equal(worker.subdomain.previews_enabled,false);
assert.equal(worker.observability.enabled,false);
assert.deepEqual(secrets,[]);
assert.equal(worker.subdomain.url,CLOUD_PROBE_URL,'Saved receipt cannot select a different network target');
const response=await cloudProbeFetch(undefined,{signal:AbortSignal.timeout(15000)});
await response.body?.cancel();
const result={checked_at:new Date().toISOString(),http_status:response.status,
  workers_dev:false,previews:false,observability:false,secret_bindings:secrets,
  final_config_expiry_ms:0,local_secret_file_removal:'denied by automatic approval review; no bypass attempted'};
await writeFile(new URL('../results/fresh-cleanup-readback.json',import.meta.url),diagnosticJson(result));
console.log(result);
assert.equal(response.status,404);
