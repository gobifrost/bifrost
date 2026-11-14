// Verify partial negative-case evidence without declaring stdout qualification.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const options=process.argv.slice(2);
if(options.length && (options.length!==2 || options[0]!=='--commit' || !/^[a-f0-9]{40}$/.test(options[1]))) {
  throw new Error('Historical source verification requires --commit <full tested SHA>');
}
const root=new URL('../../../',import.meta.url);
const read=async name=>JSON.parse(await readFile(new URL('../results/'+name,import.meta.url),'utf8'));
const first=await read('canonical-refresh-workflow-failure.json');
assert.equal(first.process_exit_code,1);
for(const [snapshot,source] of [['workflow-failure-initial-driver.txt','canonical-local.py'],
  ['workflow-failure-initial-observer.txt','canonical-refresh.mjs']]) {
  assert.equal(createHash('sha256').update(await readFile(new URL('../results/'+snapshot,import.meta.url))).digest('hex'),
    first.source_sha256['spikes/cloudflare-pyodide/backend/'+source]);
}
const receipt=await read('canonical-refresh-workflow-failure-terminal.json');
assert.equal(receipt.process_exit_code,1,'Stdout qualification must remain failed');
assert.equal(receipt.scenario,'failure');assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.physical_termination_verified,false);assert.notEqual(receipt.unknown_process_state,true);
assert.equal(receipt.qualification_error.error_type,'AssertionError');
const r=receipt.result;
for(const field of ['workflow_failure_probe','canonical_claim_verified','canonical_terminal_consumption_verified',
  'lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified','independent_document_absent',
  'structured_log_exception_redaction_verified']) assert.equal(r[field],true,field);
assert.equal(r.stdout_capture_verified,false);
assert.equal(r.canonical_failure_status,'Failed');
assert.equal(r.canonical_failure_error,'fixture failure credential=[REDACTED]');
assert.equal(r.launches,1);assert.ok(r.persisted_log_count>0);
assert.equal(r.credential_revoked_after_terminal_status,401);assert.equal(r.refresh_revoked_after_terminal_status,401);
const envelope=r.result.result;
assert.equal(envelope.success,false);assert.equal(envelope.error_type,'ValueError');assert.equal(envelope.result,null);
assert.equal(envelope.error,r.canonical_failure_error);
assert.ok(envelope.logs.some(log=>log.message.includes('structured credential=[REDACTED]')));
assert.ok(!envelope.logs.some(log=>log.message.includes('stdout credential=')));
assert.ok(!r.result.api_calls.some(call=>call.path.includes('/documents/upsert')));
for(const [path,digest] of Object.entries(receipt.source_sha256)) {
  const bytes=options.length ? (await promisify(execFile)('git',['show',`${options[1]}:${path}`],
    {cwd:root,encoding:'buffer',maxBuffer:2*1024*1024})).stdout : await readFile(new URL(path,root));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),digest,path);
}
console.log('Actual ValueError persistence, redaction and reconciliation verified; stdout qualification remains FAILED');
