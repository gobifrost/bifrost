import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/deadline-recovery/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
assert.match(await read('deadline-recovery-unit-v1/unit.txt'), /recovered canonical deadline still blocks retained settlement/);
assert.equal((await read('deadline-recovery-unit-v1/exit-code.txt')).trim(), '1');
const before = (await read('before-source.sha256')).trim().split(/\r?\n/);
assert.equal(before.length, 2);
for (const line of before) {
  const [digest, path] = line.split(/\s+/);
  const name = path.split('/').at(-1);
  const source = name === 'private_http_pool.py'
    ? execFileSync('git', ['show', 'e82e81c84bb8c4e010a2c86aa2e2735d04b6af03:spikes/cloudflare-pyodide/backend/' + name])
    : await readFile(new URL('./' + name, import.meta.url));
  assert.equal(hash(source), digest, name);
}
const raw = await read('deadline-recovery-v2/receipt.json');
assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
const report = JSON.parse(raw);
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.deepEqual(report.running_after_cleanup, []);
for (const name of ['private_http_pool.py', 'test_private_http_pool.py',
  'shared-native-pool.py', 'ct128-canonical-failure.py']) {
  assert.equal(hash(await readFile(new URL('./' + name, import.meta.url))), report.packet_sha256[name], name);
}
const probe = report.shared_pool_result;
for (const field of ['explicit_deadline_receipt_recovery_verified', 'original_deadline_receipt_failure_preserved',
  'integrated_late_result_verified', 'original_cutoff_preserved', 'timeout_committed_before_physical_settlement',
  'late_success_preserved_timeout', 'stale_deadline_owner_rejected', 'denied_document_absent',
  'terminal_obligations_released', 'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) {
  assert.equal(probe[field], true, field);
}
assert.deepEqual(probe.native_busy_counts, [2, 1, 1, 0]);
assert.equal(probe.access_revoked_before_late_result_status, 401);
assert.equal(probe.post_deadline_sdk_write_status, 410);
assert.match(await read('deadline-recovery-quality-v2/unit.txt'), /Ran 50 tests[\s\S]*\bOK\b/);
assert.match(await read('deadline-recovery-quality-v2/quality.txt'), /0 errors, 0 warnings/);
console.log('Reproduced deadline receipt recovery bug fixed; 50 native tests and real local engine/SDK timeout ACK-loss recovery preserve original failure and canonical Timeout. No deployed CPU-stop claim.');
