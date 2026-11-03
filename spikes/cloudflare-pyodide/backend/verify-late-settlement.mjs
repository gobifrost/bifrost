import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const report = JSON.parse(await readFile(new URL('../results/late-settlement/v1.json', import.meta.url)));
const prior = JSON.parse(await readFile(new URL('../results/attempt-deadline/v3.json', import.meta.url)));
assert.equal(report.packet_sha256['attempt_deadline.py'], prior.packet_sha256['attempt_deadline.py']);
const quality = JSON.parse(await readFile(new URL('../results/late-settlement/quality-checks.json', import.meta.url)));
assert.equal(quality.ruff.exit_code, 0);
assert.equal(quality.pyright.exit_code, 0);
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_state_before_cleanup.Running, true);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
assert.equal(report.provider_process_stop_verified, undefined);
for (const name of ['canonical-local.py', 'ct128-canonical-failure.py', 'attempt_deadline.py',
  'task_cleanup.py', 'invocation_stream.py']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), report.packet_sha256[name], name);
}
for (const [path, digest] of Object.entries(report.api_overlay_sha256)) {
  assert.equal(hash(await readFile(new URL('../../../' + path, import.meta.url))), digest, path);
}
const run = report.late_settlement_result;
for (const name of ['late_settlement_probe', 'canonical_timeout_preserved', 'deadline_obligation_held_until_actual_terminal',
  'late_success_received', 'late_ack_loss_retained_handle', 'actual_terminal_released_handle',
  'no_redispatch_verified', 'no_pid_fabricated', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[name], true, name);
assert.equal(run.launches, 1);
assert.equal(run.access_revoked_before_late_result_status, 401);
assert.equal(run.independent_document_value, 70);
assert.equal(run.cross_tenant_status, 403);
assert.equal(run.persisted_log_count, 1);
assert.ok(run.dispatch_accepted_monotonic < run.terminal_collected_monotonic);
const evidence = run.result;
assert.equal(evidence.runtime_handle, run.actual_opaque_handle);
assert.equal(evidence.result.success, true);
assert.equal(evidence.result.result.value, 70);
assert.equal(evidence.result.result.parent_secret_present, false);
assert.equal(evidence.result.result.credential_redaction_probe, '[REDACTED]');
assert.ok(evidence.result.duration_ms >= 40000);
assert.ok(Object.values(run.workspace_sha256).includes(evidence.result.metrics.code_hash));
assert.ok(evidence.api_calls.some(call => call.path === '/api/sdk/runtime/logs' && call.status === 410));
assert.equal(evidence.api_calls.filter(call => call.path.endsWith('/documents/upsert') && call.status === 200).length, 1);
for (const row of [report, run]) {
  assert.equal(row.cloud_invocations, 0);
  assert.equal(row.physical_termination_verified, false);
}
console.log('Actual finite late success/EOF settles the held native handle after logical Timeout, with lost-ACK replay and no canonical overwrite or per-job process kill. Local fixture only.');
