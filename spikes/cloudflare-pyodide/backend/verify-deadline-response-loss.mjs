import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const report = JSON.parse(await readFile(new URL('../results/deadline-response-loss/v1.json', import.meta.url), 'utf8'));
assert.equal(report.api_source, '1d33150eb376b71f8a81b427920eda1b0447f829');
assert.equal(report.runtime_engine_source, 'f49d4ba99cebd24aa497f1244833b940e640885f');
assert.equal(report.qualification_passed, true); // Negative boundary qualification, not executor acceptance.
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
assert.ok(report.commands.every(command => command.exit_code === 0));
for (const name of ['canonical-local.py', 'provider-response-loss.py', 'task_cleanup.py',
  'ct128-canonical-failure.py', 'authorized-local-entry.mjs', 'attempt-egress.mjs', 'fixture-admission.mjs']) {
  const bytes = execFileSync('git', ['show', `37f33d13a:spikes/cloudflare-pyodide/backend/${name}`]);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), report.packet_sha256[name], name);
}
for (const name of ['authorized-local-entry.mjs', 'attempt-egress.mjs', 'fixture-admission.mjs']) {
  assert.equal(report.packet_sha256['project/backend/' + name], report.packet_sha256[name], name);
}
assert.equal(report.packet_sha256['project/backend/generated-runtime.mjs'], 'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
const probe = report.deadline_probe_result;
for (const name of ['provider_success_before_drop', 'deadline_response_loss_probe', 'canonical_success_result_missing',
  'success_fixture_acceptance_failed', 'child_gate_bypassed_by_trusted_observer',
  'fixture_worker_cleanup_verified', 'fixture_execution_cleanup_verified']) assert.equal(probe[name], true, name);
assert.equal(probe.recovery_acceptance, false);
assert.equal(probe.credential_wall_deadline_revocation, false);
assert.equal(probe.independent_document_value, 70);
assert.equal(probe.successful_upserts, 1);
assert.equal(probe.post_deadline_native_api_write_status, 200);
assert.equal(probe.independent_post_deadline_value, 71);
assert.equal(probe.collector_error_type, 'RemoteProtocolError');
assert.equal(probe.canonical_status, 'Running');
assert.equal(probe.canonical_error_message, null);
assert.equal(probe.credential_active_pending_recovery_status, 200);
assert.equal(probe.credential_revoked_by_fixture_cleanup_status, 401);
assert.equal(probe.launches, 1);
assert.ok(Number.isFinite(Date.parse(probe.canonical_deadline)));
console.log('Actual SDK effect plus lost response across canonical cutoff preserved as uncertain; native post-deadline credential write succeeds while run is live. No accepted timeout/recovery/physical-stop claim.');
