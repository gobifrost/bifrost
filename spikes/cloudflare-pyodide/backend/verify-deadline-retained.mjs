import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const report = JSON.parse(await readFile(new URL('../results/deadline-retained/v1.json', import.meta.url), 'utf8'));
assert.equal(report.qualification_passed, true);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.equal(report.api_source, '1d33150eb376b71f8a81b427920eda1b0447f829');
assert.equal(report.runtime_engine_source, 'f49d4ba99cebd24aa497f1244833b940e640885f');
for (const name of ['canonical-local.py', 'provider-response-loss.py', 'ct128-canonical-failure.py',
  'attempt-egress.mjs', 'authorized-local-entry.mjs', 'fixture-admission.mjs', 'task_cleanup.py']) {
  assert.equal(createHash('sha256').update(await readFile(new URL('./' + name, import.meta.url))).digest('hex'), report.packet_sha256[name], name);
}
for (const name of ['attempt-egress.mjs', 'authorized-local-entry.mjs', 'fixture-admission.mjs']) {
  assert.equal(report.packet_sha256[name], report.packet_sha256['project/backend/' + name], name);
}
assert.equal(report.packet_sha256['project/backend/generated-runtime.mjs'], 'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
const result = report.deadline_probe_result;
for (const key of ['control_transport_simulated', 'provider_result_received_before_deadline',
  'upstream_retained_before_response_drop', 'reconciled_after_original_deadline', 'original_deadline_preserved',
  'duplicate_after_deadline_reused_receipt', 'terminal_ack_preserved_deadline', 'stale_retained_owner_rejected',
  'local_retained_result_consumed', 'retained_event_survived_lost_ack', 'upstream_ack_consumed',
  'local_recovery_verified', 'fixture_worker_cleanup_verified', 'fixture_execution_cleanup_verified']) assert.equal(result[key], true, key);
assert.equal(result.recovery_acceptance, false);
assert.equal(result.physical_termination_verified, false);
assert.equal(result.canonical_status, 'Running');
assert.equal(result.canonical_status_after_recovery, 'Success');
assert.equal(result.canonical_result_value, 70);
assert.equal(result.independent_document_value, 70);
assert.equal(result.successful_upserts, 1);
assert.equal(result.launches, 1);
assert.equal(result.collector_error_type, 'RemoteProtocolError');
assert.equal(result.provider_execution_id, result.canonical_execution_id);
assert.equal(result.provider_worker_id, result.canonical_worker_id);
assert.ok(Object.values(result.workspace_sha256).includes(result.observed_code_hash));
assert.equal(result.credential_active_after_owner_reconnect_status, 200);
assert.equal(result.credential_revoked_by_canonical_success_status, 401);
assert.equal(result.refresh_revoked_by_canonical_success_status, 401);
assert.equal(result.credential_revoked_by_fixture_cleanup_status, 401);
assert.ok(result.persisted_log_count > 0);
console.log('Local retained result crosses original cutoff: owner fencing, duplicate receipt, canonical success, lost ACK replay, credential revocation and cleanup pass; no cloud/cancellation acceptance.');
