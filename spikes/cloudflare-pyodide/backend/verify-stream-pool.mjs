import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(await readFile(new URL('../results/stream-pool/v1.json', import.meta.url)));
assert.equal(receipt.qualification_passed, true);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.provider_state_before_cleanup.OOMKilled, false);
for (const name of ['canonical-local.py', 'authorized-local-entry.mjs', 'ct128-canonical-failure.py',
  'invocation-stream.mjs', 'invocation_stream.py', 'task_cleanup.py']) {
  assert.equal(hash(execFileSync('git', ['show', `2487819b5:spikes/cloudflare-pyodide/backend/${name}`])), receipt.packet_sha256[name], name);
}
for (const name of ['authorized-local-entry.mjs', 'invocation-stream.mjs']) {
  assert.equal(receipt.packet_sha256['project/backend/' + name], receipt.packet_sha256[name]);
}
assert.equal(receipt.packet_sha256['project/backend/generated-runtime.mjs'],
  'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
const run = receipt.physical_pool_result;
for (const name of ['stream_pool_probe', 'dispatch_ack_before_workflow_completion', 'no_pid_fabricated',
  'canonical_claim_verified', 'canonical_terminal_consumption_verified', 'lost_ack_reconnect_verified',
  'native_opaque_route_released_after_terminal_ack', 'fixture_worker_cascade_cleanup_verified']) {
  assert.equal(run[name], true, name);
}
assert.equal(run.dispatch_ack_after_workflow_completion, false);
assert.ok(run.dispatch_accepted_monotonic < run.terminal_collected_monotonic);
assert.equal(run.actual_opaque_handle, run.result.runtime_handle);
assert.equal(run.opaque_invocation_count, 1);
assert.equal(run.launches, 1);
assert.equal(run.independent_document_value, 70);
assert.equal(run.cross_tenant_status, 403);
assert.equal(run.credential_revoked_after_terminal_status, 401);
assert.equal(run.result.result.result.parent_secret_present, false);
assert.equal(run.result.result.result.credential_redaction_probe, '[REDACTED]');
assert.equal(run.result.api_calls.filter(call => call.path.endsWith('/documents/upsert') && call.status === 200).length, 1);
for (const report of [receipt, run]) {
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.physical_termination_verified, false);
}
console.log('Actual local upstream pool: early opaque ACK, awaited engine/SDK result, canonical lost-ACK replay and cleanup pass. No cloud or physical-stop acceptance.');
