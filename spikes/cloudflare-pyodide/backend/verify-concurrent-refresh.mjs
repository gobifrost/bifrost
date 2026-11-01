// Offline contract/provenance checks for the mixed-call refresh experiment.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const args=process.argv.slice(2);
if(args.length && (args.length!==2 || args[0]!=='--commit' || !/^[a-f0-9]{40}$/.test(args[1]))) {
  throw new Error('Use no arguments or --commit <full tested SHA>');
}
const historicalCommit=args[1],exec=promisify(execFile);
const root=new URL('../../../',import.meta.url);
const read=async name=>JSON.parse(await readFile(new URL('../results/'+name,import.meta.url),'utf8'));
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const initial=await read('canonical-refresh-concurrent.json');
assert.equal(initial.process_exit_code,1);assert.equal(initial.phase,'observed');
assert.equal(initial.unknown_process_state,false); // Runner state, not isolate termination.
assert.equal(initial.physical_termination_verified,false);
assert.equal(hash(await readFile(new URL('../results/concurrent-refresh-initial-adapter.txt',import.meta.url))),
  initial.source_sha256['spikes/cloudflare-pyodide/backend/pyodide_refresh.py']);
for(const [snapshot,source] of [['concurrent-refresh-driver.txt','canonical-local.py'],
  ['concurrent-refresh-observer.txt','canonical-refresh.mjs']]) {
  assert.equal(hash(await readFile(new URL('../results/'+snapshot,import.meta.url))),
    initial.source_sha256['spikes/cloudflare-pyodide/backend/'+source]);
}
assert.equal((await read('concurrent-refresh-initial-manifest.json')).payload_sha256,initial.payload_sha256);
const mixed=await read('canonical-refresh-concurrent-cooperative.json');
const cold=await read('canonical-refresh-challenge-cooperative.json');
for(const receipt of [mixed,cold]) {
  assert.equal(receipt.process_exit_code,0);assert.notEqual(receipt.unknown_process_state,true);
  assert.equal(receipt.cloud_invocations,0);assert.equal(receipt.physical_termination_verified,false);
  const r=receipt.result;
  for(const field of ['canonical_claim_verified','canonical_terminal_consumption_verified',
    'lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified']) assert.equal(r[field],true,field);
  assert.equal(r.launches,1);assert.equal(r.independent_document_value,70);
  assert.equal(r.cross_tenant_status,403);assert.equal(r.credential_revoked_after_terminal_status,401);
  assert.equal(r.refresh_revoked_after_terminal_status,401);
  assert.equal(r.result.result.result.credential_redaction_probe,'[REDACTED]');
  assert.equal(r.result.result.result.parent_secret_present,false);
  assert.deepEqual(r.result.api_calls.filter(c=>c.path==='/auth/refresh'),[{method:'POST',path:'/auth/refresh',status:200}]);
  for(const [path,digest] of Object.entries(receipt.source_sha256)) {
    const bytes=historicalCommit ? (await exec('git',['show',`${historicalCommit}:${path}`],
      {cwd:root,encoding:'buffer',maxBuffer:2*1024*1024})).stdout : await readFile(new URL(path,root));
    assert.equal(hash(bytes),digest,path);
  }
  const manifest=await read(receipt.runtime_manifest);
  assert.equal(manifest.payload_sha256,receipt.payload_sha256);
  assert.equal(manifest.files['pyodide_refresh.py'],receipt.source_sha256['spikes/cloudflare-pyodide/backend/pyodide_refresh.py']);
}
assert.equal(mixed.result.concurrent_refresh_probe,true);
const value=mixed.result.result.result.result;
assert.equal(value.refresh_callers,3);assert.equal(value.same_replacement,true);
assert.equal(value.synchronous_callers,1);assert.equal(value.asynchronous_callers,2);
assert.equal(cold.result.expired_access_probe,true);assert.equal(cold.result.preflight_challenge_status,401);
const [denied,refresh,replay]=cold.result.result.api_calls;
assert.equal(denied.status,401);assert.equal(refresh.path,'/auth/refresh');
assert.equal(replay.path,denied.path);assert.equal(replay.status,200);
assert.notEqual(mixed.result.result.result.execution_id,cold.result.result.result.execution_id);
console.log('Mixed sync/async credential convergence and cold challenge regression verified against actual local canonical receipts');
