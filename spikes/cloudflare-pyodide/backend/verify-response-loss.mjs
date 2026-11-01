// Passing this verifier characterizes failed recovery; it is not acceptance.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const args=process.argv.slice(2);
if(args.length && (args.length!==2 || args[0]!=='--commit' || !/^[a-f0-9]{40}$/.test(args[1]))) {
  throw new Error('Use no arguments or --commit with an exact historical commit');
}
const root=new URL('../../../',import.meta.url);
const hash=b=>createHash('sha256').update(b).digest('hex');
const initial=JSON.parse(await readFile(new URL('../results/canonical-refresh-response-loss.json',import.meta.url)));
assert.equal(initial.process_exit_code,1);
assert.equal(initial.result.error_type,'AttributeError');
assert.equal(hash(await readFile(new URL('../results/response-loss-initial-driver.txt',import.meta.url))),
  initial.source_sha256['spikes/cloudflare-pyodide/backend/provider-response-loss.py']);
const receipt=JSON.parse(await readFile(new URL('../results/canonical-refresh-response-loss-observed.json',import.meta.url)));
assert.equal(receipt.process_exit_code,0);
assert.notEqual(receipt.unknown_process_state,true);
const r=receipt.result;
assert.equal(r.recovery_acceptance,false);
assert.equal(r.cloud_invocations,0);
assert.equal(r.physical_termination_verified,false);
for(const key of ['provider_response_loss_probe','provider_success_before_drop',
  'canonical_success_result_missing','success_fixture_acceptance_failed','fixture_worker_cleanup_verified']) {
  assert.equal(r[key],true,key);
}
assert.equal(r.independent_document_value,70);
assert.equal(r.successful_upserts,1);
assert.ok(r.response_bytes_discarded>0);
assert.equal(r.collector_error_type,'RemoteProtocolError');
assert.equal(r.canonical_status,'Failed');
assert.equal(r.canonical_error_message,'Server disconnected without sending a response.');
assert.equal(r.credential_revoked_after_failure_status,401);
assert.equal(r.launches,1);
for(const [path,digest] of Object.entries(receipt.source_sha256)) {
  const source=args.length ? execFileSync('git',['show',`${args[1]}:${path}`],{cwd:root}) : await readFile(new URL(path,root));
  assert.equal(hash(source),digest,path);
}
console.log('Response-loss boundary verified: successful SDK effects, canonical Failed, no recovered success or replay');
