import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const read = path => readFileSync(new URL(path, import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(read('../results/https-disconnect/v8.json'));
for (const [source, packet] of [
  ['https-disconnect-entry.mjs', 'project/backend/approved-https-entry.mjs'],
  ['https-disconnect-probe.mjs', 'project/backend/approved-https-probe.mjs'],
  ['invocation-stream.mjs', 'project/backend/invocation-stream.mjs'],
  ['approved-https-candidate.mjs', 'project/backend/approved-https-candidate.mjs'],
  ['attempt-egress.mjs', 'project/backend/attempt-egress.mjs'],
  ['ct128-sdk-probe.py', 'ct128-sdk-probe.py'],
]) assert.equal(hash(read(source)), receipt.packet_sha256[packet], source);
for (const revision of [7, 8]) {
  const failed = JSON.parse(read(`../results/https-disconnect/v${revision}.json`));
  assert.equal(failed.observation, undefined);
  assert.equal(failed.running_after_cleanup, 'false');
  assert.equal(failed.provider_state_before_cleanup.OOMKilled, false);
  assert.equal(failed.engine_sdk_execution_verified, false);
  assert.equal(failed.cloud_invocations, 0);
}
const earlier = JSON.parse(read('../results/https-disconnect/v7.json'));
assert.equal(hash(read('../results/https-disconnect/v7-probe.mjs')), earlier.packet_sha256['project/backend/approved-https-probe.mjs']);
const observed = JSON.parse(read('../results/https-disconnect/v8-observation.json'));
assert.equal(observed.status, 500); // Qualification failed; not a provider HTTP status.
const body = observed.body, control = body.observations.control;
assert.deepEqual(control.terminal.evidence.result.result.statuses, [200, 200, 200]);
assert.equal(control.eof_observed, true);
assert.deepEqual(control.after, {closed: true, execute_returned: true});
assert.equal(body.at_failure.phase, 'awaiting_gate_close');
assert.deepEqual(body.at_failure.parent, {closed: false, execute_returned: false});
assert.equal(body.at_failure.received, true);
assert.equal(body.at_failure.committed, false);
assert.deepEqual(body.at_failure.effects, ['/before']);
assert.deepEqual(body.after_failure_release.effects, ['/before', '/after']);
assert.equal(body.after_failure_release.committed, true);
assert.deepEqual(body.after_failure_release.parent, {closed: true, execute_returned: true});
assert.deepEqual(body.after_failure_release.events, ['hold_received', 'parent_release', 'hold_committed']);
assert.equal(body.physical_termination_verified, false);
assert.equal(body.canonical_recovery_verified, false);
assert.equal(body.observations.disconnect, undefined);
console.log('Failed local HTTP cancellation delivery evidence verified; executor cancellation acceptance remains FAILED.');
