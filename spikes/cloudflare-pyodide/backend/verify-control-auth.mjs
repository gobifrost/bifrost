// Offline only: no Docker, API or remote execution.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const args=process.argv.slice(2);
if(args.length && (args.length!==2 || args[0]!=='--commit' || !/^[a-f0-9]{40}$/.test(args[1]))) {
  throw new Error('Use no arguments or --commit with an exact historical commit');
}
const root=new URL('../../../',import.meta.url);
const receipt=JSON.parse(await readFile(new URL('../results/canonical-refresh-control-auth.json',import.meta.url)));
assert.equal(receipt.phase,'observed');
assert.equal(receipt.process_exit_code,0);
assert.notEqual(receipt.unknown_process_state,true);
assert.equal(receipt.runtime_manifest,null);
assert.equal(receipt.payload_sha256,null);
const r=receipt.result;
for(const key of ['control_auth_probe','invalid_signature_did_not_acquire_owner',
  'nonce_replay_did_not_advance_owner','empty_handlers_preserved','remained_starting',
  'linux_templates_unstarted','fixture_worker_cleanup_verified']) assert.equal(r[key],true,key);
for(const key of ['invalid_signature_close','nonce_replay_close','superseded_socket_close','stale_generation_close']) {
  assert.equal(r[key],1008,key);
}
assert.ok(r.signed_hello_generation>=1);
assert.ok(r.signed_reconnect_generation>r.signed_hello_generation);
assert.equal(r.claims_observed,0);
assert.equal(r.actual_worker_invocations,0);
assert.equal(r.cloud_invocations,0);
assert.equal(r.physical_termination_verified,false);
assert.equal(r.inventory_sent,false);
assert.equal(r.execution_admission_qualified,false);
assert.equal(r.signed_hello_material_retained,false);
for(const [path,digest] of Object.entries(receipt.source_sha256)) {
  const source=args.length ? execFileSync('git',['show',`${args[1]}:${path}`],{cwd:root}) : await readFile(new URL(path,root));
  assert.equal(createHash('sha256').update(source).digest('hex'),digest,path);
}
console.log('Live isolated control authentication/fencing verified; empty handlers, no inventory or execution admission');
