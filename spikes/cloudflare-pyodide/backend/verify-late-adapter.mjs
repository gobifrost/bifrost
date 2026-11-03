import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/late-adapter/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const failed = JSON.parse(await read('late-adapter-v1/receipt.json'));
assert.equal(failed.qualification_passed, false);
assert.equal(failed.commands.at(-1).exit_code, 1);
assert.equal(hash(await read('v1-fixture.txt')), failed.packet_sha256['shared-native-pool.py']);
assert.deepEqual(failed.running_after_cleanup, []);
const raw = await read('late-adapter-v2/receipt.json');
assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
const report = JSON.parse(raw);
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.provider_state_before_cleanup.Running, true);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
assert.deepEqual(report.running_after_cleanup, []);
for (const name of ['shared-native-pool.py', 'ct128-canonical-failure.py']) {
  const tested = execFileSync('git', ['show', '50093038cbc4f9ddde61264c453393464a717485:spikes/cloudflare-pyodide/backend/' + name]);
  assert.equal(hash(tested), report.packet_sha256[name], name);
}
const prior = JSON.parse(await readFile(new URL('../results/owned-deadline/auto-delivery-v2/receipt.json', import.meta.url)));
for (const name of ['private_http_pool.py', 'test_private_http_pool.py', 'attempt_deadline.py',
  'test_attempt_deadline.py', 'attempt_journal.py', 'workspace_prepare.py']) {
  assert.equal(report.packet_sha256[name], prior.packet_sha256[name], name + ' retained candidate');
}
const probe = report.shared_pool_result;
for (const field of ['integrated_late_result_verified', 'original_cutoff_preserved',
  'timeout_committed_before_physical_settlement', 'late_success_preserved_timeout',
  'stale_deadline_owner_rejected', 'post_deadline_sdk_write_denied', 'denied_document_absent',
  'automatic_terminal_delivery_verified', 'terminal_obligations_released',
  'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) {
  assert.equal(probe[field], true, field);
}
assert.equal(probe.post_deadline_sdk_write_status, 410);
assert.equal(probe.access_revoked_before_late_result_status, 401);
assert.deepEqual(probe.native_busy_counts, [2, 1, 1, 0]);
for (const [id, attempt] of Object.entries(probe.attempts)) {
  assert.equal(probe.launches[id], 1);
  assert.equal(attempt.access_after_terminal_status, 401);
  assert.equal(attempt.evidence.result.success, true);
  if (attempt.label === 'fails-first') {
    assert.equal(attempt.canonical_status, 'Timeout');
    assert.equal(attempt.canonical_result, null);
    assert.equal(attempt.evidence.result.result.value, 70);
    assert.ok(attempt.evidence.result.duration_ms > 30000);
    assert.ok(attempt.evidence.api_calls.some(call => call.status === 410 && call.path.includes('/documents/upsert')));
  } else {
    assert.equal(attempt.canonical_status, 'Success');
    assert.equal(attempt.canonical_result.value, 78);
  }
}
assert.match(await read('late-adapter-quality-v2/quality.txt'), /0 errors, 0 warnings/);
console.log('Real local integrated Timeout, revoked API credential, denied late SDK write, unchanged late-result reconciliation and held-capacity settlement verified. No CPU-stop or deployed acceptance claim.');
