import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../results/qualified-runtime-stage/',import.meta.url);
const report=JSON.parse(readFileSync(new URL('assembly-result.json',root)));
const hash=value=>createHash('sha256').update(value).digest('hex');
assert.equal(report.source_sha256,'61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731');
assert.equal(report.provenance.bundle_sha256,report.source_sha256);
assert.equal(report.provenance.bundle_bytes,15287043);
assert.equal(report.provenance.module_count,466);
assert.equal(report.constructed_module_count,468);
for(const [name,digest] of Object.entries(report.staging_sources_sha256))
  assert.equal(hash(readFileSync(new URL(name,import.meta.url))),digest);
for(const [name,digest] of Object.entries(report.provenance.loader_files_sha256))
  assert.equal(hash(readFileSync(new URL('../backend/'+name,import.meta.url))),digest);
assert.equal(report.provenance.artifact_bytes_preserved,true);
assert.equal(report.existing_output_refused,true);
assert.equal(report.sdk_cleanup_injected,true);
assert.equal(report.python_executed,false);
assert.equal(report.cloud_invocations,0);
assert.match(readFileSync(new URL('tests.txt',root),'utf8'),/pass 4/);
console.log('Exact real qualified artifact staged byte-for-byte and full Node loader imported; Python/deployed qualification unchanged.');
