import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const read=async name=>JSON.parse((await readFile(new URL('../results/'+name,import.meta.url),'utf8')).replace(/^\uFEFF/,''));
const worker=await read('fresh-final-worker.json');
const secrets=await read('fresh-final-secrets.json');
// Normalize PowerShell CLI captures to UTF-8/LF without changing API values.
for(const [name,value] of [['fresh-final-worker',worker],['fresh-final-secrets',secrets]]) {
  await writeFile(new URL('../results/'+name+'.json',import.meta.url),JSON.stringify(value,null,2)+'\n');
}
assert.equal(worker.subdomain.enabled,false);
assert.equal(worker.subdomain.previews_enabled,false);
assert.equal(worker.observability.enabled,false);
assert.deepEqual(secrets,[]);
const response=await fetch(worker.subdomain.url,{signal:AbortSignal.timeout(15000)});
await response.body?.cancel();
const result={checked_at:new Date().toISOString(),http_status:response.status,
  workers_dev:false,previews:false,observability:false,secret_bindings:secrets,
  final_config_expiry_ms:0,local_secret_file_removal:'denied by automatic approval review; no bypass attempted'};
await writeFile(new URL('../results/fresh-cleanup-readback.json',import.meta.url),JSON.stringify(result,null,2)+'\n');
console.log(result);
assert.equal(response.status,404);
