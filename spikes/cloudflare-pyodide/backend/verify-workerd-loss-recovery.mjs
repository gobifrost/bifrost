// Local recovery/late-result evidence; no loss detection or physical-stop claim.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const bytes = readFileSync(new URL('../results/workerd-loss-recovery/receipt.json', import.meta.url));
assert.equal(hash(bytes), '62bcd195b85b1d5a9d63713e4ffdbc8a3c514e90f43662b1af05f94a22bfce97');
const report = JSON.parse(bytes);
assert.equal(report.qualification_passed, true);
assert.equal(report.cloudflare_runtime_qualified, false);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.cloud_invocations, 0);
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
assert.equal(report.packet_sha256['canonical-local.py'],
  hash(execFileSync('git', ['show',
    '81799e546ad55eecd94ae0130b5e1b0494780697:spikes/cloudflare-pyodide/backend/canonical-local.py'],
  {cwd: fileURLToPath(new URL('../../../', import.meta.url))})));
const run = report.canonical_result;
for (const key of ['canonical_claim_verified', 'worker_loss_recovery_verified',
  'late_success_ignored_verified', 'recovery_not_requeued_verified',
  'lost_worker_log_recovery_verified',
  'lost_ack_reconnect_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[key], true, key);
assert.equal(run.canonical_terminal_consumption_verified, false);
assert.equal(run.canonical_recovery_status, 'Failed');
assert.equal(run.result.result.success, true);
assert.equal(run.result.result.result.value, 70);
assert.equal(run.independent_document_value, 70);
assert.equal(run.launches, 1);
assert.equal(run.access_after_loss_status, 401);
assert.equal(run.refresh_after_loss_status, 401);
assert.equal(run.credential_revoked_after_terminal_status, 401);
assert.equal(run.log_rows_before_terminal_drain, 0);
assert.equal(run.log_rows_after_terminal_drain, 2);
assert.equal(run.terminal_log_drain.flushed, 2);
assert.equal(run.repeated_terminal_log_drain.flushed, 0);
assert.equal(run.result.api_calls.filter(call => call.path.endsWith('/documents/upsert') && call.status === 200).length, 1);
const initialBytes = readFileSync(new URL('../results/workerd-loss-recovery/initial-receipt.json', import.meta.url));
assert.equal(hash(initialBytes), '4d6a3851fed0056fa300d7b8eb0754d2e38ad7f1c2a98a1ccc701145490637b3');
assert.equal(JSON.parse(initialBytes).canonical_result.persisted_log_count, 0);
console.log('Actual workerd success stayed subordinate to canonical lost-worker Failed state; access/refresh denied before late-result delivery, no redispatch.');
