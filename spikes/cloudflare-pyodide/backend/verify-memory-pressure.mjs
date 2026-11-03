import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root = new URL('../', import.meta.url);
const read = name => JSON.parse(readFileSync(new URL('results/memory-pressure/' + name, root), 'utf8').replace(/^\uFEFF/, ''));
const failed = read('receipt-v1.json');
assert.equal(failed.qualification_passed, false);
assert.equal(failed.commands.at(-1).exit_code, 1);
const receipt = read('receipt-v2.json');
for (const name of ['shared-native-pool.py', 'ct128-canonical-failure.py']) {
  assert.equal(createHash('sha256').update(readFileSync(new URL('backend/' + name, root))).digest('hex'),
    receipt.packet_sha256[name], name);
}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.provider_state_before_cleanup.OOMKilled, false);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
const proof = receipt.shared_pool_result;
assert.equal(proof.bounded_memory_outcome, 'allocated_160mib');
assert.equal(proof.bounded_memory_probe_bytes, 167772160);
assert.equal(proof.bounded_memory_matching_log_count, 2);
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
for (const key of ['failed_replay_unchanged_verified', 'stale_terminal_owner_rejected',
  'duplicate_dispatch_reused_peer', 'canonical_settled_accounting_verified',
  'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
const subject = Object.values(proof.attempts).find(value => value.label === 'fails-first');
const peer = Object.values(proof.attempts).find(value => value.label === 'succeeds-second');
assert.equal(subject.canonical_status, 'Failed');
assert.match(subject.canonical_error, /bounded_memory_probe=allocated_160mib$/);
assert.ok(subject.persisted_logs.includes('bounded_memory_probe=allocated_160mib'));
assert.ok(subject.persisted_logs.includes('ValueError: bounded_memory_probe=allocated_160mib'));
assert.equal(peer.canonical_status, 'Success');
assert.equal(peer.canonical_result.value, 78);
assert.equal(subject.access_after_terminal_status, 401);
assert.equal(peer.access_after_terminal_status, 401);
console.log('Local 160MiB allocation/peer/reconciliation observed; deployed 128MiB enforcement NOT qualified.');
