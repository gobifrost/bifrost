// Retained local runtime evidence, never a physical-stop acceptance gate.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {classifyTimedCancellation} from './termination-evidence.mjs';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const bytes = readFileSync(new URL('../results/rpc-termination/receipt.json', import.meta.url));
assert.equal(sha(bytes), '96378ac2256611748f268de2a221fdfb975d96706a5cf7aa5924848672e6ab1d');
const report = JSON.parse(bytes);
assert.equal(report.bundle_sha256, '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260');
assert.equal(report.packet_sha256['backend/guest-rpc-termination-entry.mjs'],
  sha(readFileSync(new URL('guest-rpc-termination-entry.mjs', import.meta.url))));
assert.equal(report.observer_exit_code, 0);
assert.equal(report.state_before_cleanup.OOMKilled, false);
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.volumes_deleted, false);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.execution_admission_qualified, false);
const records = report.observations.records;
assert.deepEqual(records.map(record => record.mode), ['control', 'catch', 'abort']);
assert.ok(records[0].events.some(event => event.event === 'after'));
const abort = records[2];
assert.equal(classifyTimedCancellation(abort.events).classification, 'late_effect_after_abort');
assert.equal(abort.physical_termination_verified, false);
const requested = abort.events.find(event => event.event === 'abort_requested').elapsed_ms;
const after = abort.events.find(event => event.event === 'after').elapsed_ms;
assert.ok(after > requested);
console.log(`Actual local Python RPC emitted after abort by ${(after - requested).toFixed(3)} ms; physical termination remains unqualified.`);
