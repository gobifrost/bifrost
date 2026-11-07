import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
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
const entry = execFileSync('git', ['show', 'd9fa3159551847d6cff1ca6318ea396f2d75ab1b:spikes/cloudflare-pyodide/dev/executor/canonical-entry.mjs']);
assert.equal(createHash('sha256').update(entry).digest('hex'), manifest.files_sha256['dev/executor/canonical-entry.mjs']);
console.log('Historical d9fa315 guarded entry passed locally; failed controls retained; this receipt does not qualify revised source.');
