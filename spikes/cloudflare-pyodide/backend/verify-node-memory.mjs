// Offline raw-evidence check; never infers Cloudflare memory or termination acceptance.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const bytes=readFileSync(new URL('../results/node-memory-resume/success.json',import.meta.url));
assert.equal(createHash('sha256').update(bytes).digest('hex'),'23f5739a64f893f9bde6166e4b270dd83b01de4ab7ca0d9432da35baef90177c');
const receipt=JSON.parse(bytes);
assert.equal(receipt.qualification_passed,true);
assert.equal(receipt.cloudflare_runtime_qualified,false);
assert.equal(receipt.physical_termination_verified,false);
assert.equal(receipt.cloud_invocations,0);
assert.deepEqual(receipt.running_after_cleanup,[]);
assert.equal(receipt.volumes_deleted,false);
const run=receipt.canonical_result;
assert.equal(run.integration_secret_decryption_and_redaction_verified,true);
assert.equal(run.canonical_terminal_consumption_verified,true);
assert.equal(run.lost_ack_reconnect_verified,true);
assert.equal(run.launches,1);
const samples=run.result.memory_samples;
assert.deepEqual(samples.map(s=>s.phase),['core_loaded','upstream_bootstrap_complete','sdk_execution_complete']);
assert.deepEqual(samples.map(s=>s.wasm_linear_memory_capacity_bytes),[31457280,45350912,45350912]);
for(const sample of samples) {
  assert.ok(Number.isSafeInteger(sample.wasm_linear_memory_capacity_bytes));
  assert.equal(sample.wasm_linear_memory_capacity_bytes%65536,0);
  assert.ok(sample.node_process.rss>0);
}
console.log('Guest WASM capacity evidence verified: final observed 43.25 MiB; Cloudflare total isolate memory remains unmeasured.');
