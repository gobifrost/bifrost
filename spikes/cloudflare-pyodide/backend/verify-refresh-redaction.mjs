import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url);
const raw = readFileSync(new URL('results/refresh-redaction/receipt.json', root), 'utf8').replace(/^\uFEFF/, '');
assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
const receipt = JSON.parse(raw);
for (const name of ['shared-native-pool.py', 'ct128-canonical-failure.py']) {
  const source = name === 'ct128-canonical-failure.py'
    ? execFileSync('git', ['show', '9c0593b75908c15876760afe2de7083049d07820:spikes/cloudflare-pyodide/backend/' + name])
    : readFileSync(new URL('backend/' + name, root));
  assert.equal(createHash('sha256').update(source).digest('hex'),
    receipt.packet_sha256[name], name);
}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
const proof = receipt.shared_pool_result;
for (const key of ['run_refresh_generation_redaction_verified', 'both_real_sdk_effects_held_concurrently',
  'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified', 'duplicate_dispatch_reused_peer',
  'canonical_settled_accounting_verified', 'mux_retired_after_canonical_ack',
  'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
const subject = Object.values(proof.attempts).find(value => value.label === 'fails-first');
const peer = Object.values(proof.attempts).find(value => value.label === 'succeeds-second');
const marker = 'refresh_probe original=[REDACTED] replacement=[REDACTED] refresh=[REDACTED]';
assert.equal(subject.canonical_status, 'Failed');
assert.equal(subject.canonical_error, marker);
assert.equal(subject.persisted_logs.filter(message => message === marker).length, 2);
assert.ok(subject.persisted_logs.includes('ValueError: ' + marker));
assert.ok(subject.evidence.api_calls.some(call => call.path === '/auth/refresh' && call.status === 200));
assert.equal(peer.canonical_status, 'Success');
assert.equal(peer.canonical_result.value, 78);
assert.equal(subject.access_after_terminal_status, 401);
assert.equal(peer.access_after_terminal_status, 401);
console.log('Real local run refresh/redaction/canonical replay verified; deployed containment remains unqualified.');
