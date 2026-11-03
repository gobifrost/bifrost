import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root=new URL('../results/https-capability/',import.meta.url);
const read=name=>readFileSync(new URL(name,root),'utf8');
const receipt=JSON.parse(read('receipt.json'));
const hash=value=>createHash('sha256').update(value).digest('hex');
for(const [file,field] of [['private_http_pool.py','private_pool_sha256'],['test_private_http_pool.py','tests_sha256'],['test-api-route.mjs','route_sha256']]) {
  const bytes=file==='test-api-route.mjs' ? readFileSync(new URL(file,import.meta.url))
    : execFileSync('git',['show','338eac91a8ca72bd38ab479c9d8fb200f5f8ae27:spikes/cloudflare-pyodide/backend/'+file]);
  assert.equal(hash(bytes),receipt[field]);
}
assert.match(read('before.txt'),/FAILED \(failures=2\)/);
assert.equal(JSON.parse(read('before-raw.json')).raw_output.replace(/[ \t]+$/gm,''),read('before.txt'));
assert.match(read('after.txt'),/Ran 31 tests[\s\S]*\bOK\b/);
assert.match(read('pyright.txt'),/0 errors, 0 warnings/);
assert.match(read('ruff.txt'),/All checks passed/);
assert.match(read('routes.txt'),/pass 4/);
assert.equal(receipt.external_https_implemented,false);
assert.equal(receipt.public_admission_enabled,false);
assert.equal(receipt.cloud_invocations,0);
console.log('False direct-HTTPS declaration refused before effects; supported SDK declaration preserved; external egress remains unimplemented.');
