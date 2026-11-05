import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const read = name => JSON.parse(readFileSync(new URL(name, import.meta.url), 'utf8'));
for (const revision of [2, 3]) {
  const failed = read(`guarded-local-v${revision}.json`);
  assert.equal(failed.qualification_passed, false);
  assert.deepEqual(failed.running_after_cleanup, []);
}
const passed = read('guarded-local-v4.json');
assert.equal(passed.qualification_passed, true);
assert.deepEqual(passed.command_exit_codes, [0, 0, 0, 0]);
assert.deepEqual(passed.launch_counts, [1, 1]);
assert.deepEqual(passed.attempts.map(row => row.canonical_status), ['Failed', 'Success']);
assert.ok(passed.attempts.every(row => row.sdk_close_returned && row.access_after_terminal_status === 401));
for (const name of ['duplicate_dispatch_reused_peer', 'lost_ack_reconnect_verified',
  'stale_terminal_owner_rejected', 'canonical_terminal_reconciliation_verified',
  'canonical_settled_accounting_verified', 'fixture_worker_cascade_cleanup_verified']) {
  assert.equal(passed.contract_observations[name], true, name);
}
assert.deepEqual(passed.running_after_cleanup, []);
assert.equal(passed.cloud_invocations, 0);
assert.equal(passed.physical_termination_verified, false);
const manifest = read('guarded-local-source-v4.json');
const entry = readFileSync(new URL('./executor/canonical-entry.mjs', import.meta.url));
assert.equal(createHash('sha256').update(entry).digest('hex'), manifest.files_sha256['dev/executor/canonical-entry.mjs']);
console.log('Latest guarded entry passed canonical engine/SDK locally; failed controls retained; no deployed or physical-stop claim.');
