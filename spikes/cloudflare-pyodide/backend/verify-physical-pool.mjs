import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const read = async path => JSON.parse(await readFile(new URL(path, import.meta.url), 'utf8'));
const hash = value => createHash('sha256').update(value).digest('hex');
const unit = await read('../results/physical-handle/v1.json');
assert.equal(unit.qualification_passed, true);
assert.deepEqual(unit.before, {cases: 1, failures: 1, errors: 0, skipped: 0});
assert.deepEqual(unit.after, {cases: 67, failures: 0, errors: 0, skipped: 0});
assert.equal(unit.commands[1].exit_code, 1);
assert.ok(unit.commands.filter((_, index) => index !== 1).every(command => command.exit_code === 0));
assert.deepEqual(unit.running_after_cleanup, []);
assert.equal(unit.execution_admission_qualified, false);
const mappings = {'protocol.py': '../../../api/src/worker/remote/protocol.py',
  'remote_pool.py': '../../../api/src/worker/remote/remote_pool.py',
  'test_pyodide_physical_handle.py': '../../../api/tests/unit/dev/test_pyodide_physical_handle.py',
  'ct128-physical-handle.py': './ct128-physical-handle.py'};
for (const [name, path] of Object.entries(mappings)) {
  assert.equal(hash(await readFile(new URL(path, import.meta.url))), unit.packet_sha256[name], name);
}
const live = await read('../results/physical-pool/v1.json');
assert.equal(live.qualification_passed, true);
assert.ok(live.commands.every(command => command.exit_code === 0));
assert.deepEqual(live.running_after_cleanup, []);
assert.equal(live.provider_state_before_cleanup.OOMKilled, false);
for (const name of ['canonical-local.py', 'authorized-local-entry.mjs', 'ct128-canonical-failure.py', 'task_cleanup.py']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), live.packet_sha256[name], name);
}
assert.equal(live.packet_sha256['project/backend/authorized-local-entry.mjs'], live.packet_sha256['authorized-local-entry.mjs']);
assert.equal(live.packet_sha256['project/backend/generated-runtime.mjs'], 'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
const overlay = await read('../results/physical-pool/api-overlay-readback.json');
for (const [path, digest] of Object.entries(overlay.source_sha256)) assert.equal(digest, unit.packet_sha256[path.split('/').at(-1)]);
const run = live.physical_pool_result;
for (const name of ['physical_pool_probe', 'no_pid_fabricated', 'canonical_claim_verified',
  'canonical_terminal_consumption_verified', 'lost_ack_reconnect_verified',
  'native_opaque_route_released_after_terminal_ack', 'fixture_worker_cascade_cleanup_verified',
  'controller_memory_not_isolate_memory', 'dispatch_ack_after_workflow_completion']) assert.equal(run[name], true, name);
assert.equal(run.actual_opaque_handle, run.result.runtime_handle);
assert.match(run.actual_opaque_handle, /^[a-f0-9-]{36}$/);
assert.equal(run.opaque_invocation_count, 1);
assert.equal(run.launches, 1);
assert.equal(run.independent_document_value, 70);
assert.equal(run.cross_tenant_status, 403);
assert.equal(run.credential_revoked_after_terminal_status, 401);
assert.ok(run.persisted_log_count > 0);
assert.equal(run.result.result.result.value, 70);
assert.equal(run.result.result.result.parent_secret_present, false);
assert.equal(run.result.result.result.credential_redaction_probe, '[REDACTED]');
assert.ok(Object.values(run.workspace_sha256).includes(run.result.result.metrics.code_hash));
for (const key of ['peak_memory_bytes', 'peak_process_rss_bytes', 'cpu_total_seconds']) assert.equal(run.result.result.metrics[key], null);
assert.equal(run.result.api_calls.filter(call => call.path.endsWith('/documents/upsert') && call.status === 200).length, 1);
for (const report of [unit, live, run]) {
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.physical_termination_verified, false);
}
console.log('67 native cases plus actual upstream pool -> local Pyodide engine/SDK -> canonical lost-ACK replay pass without a fake PID. Public admission and physical cancellation remain unqualified.');
