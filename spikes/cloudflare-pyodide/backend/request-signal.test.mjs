import test from 'node:test';
import assert from 'node:assert/strict';
import {forwardSignalProbe} from './request-signal-probe.mjs';
import {classifySignalRecord} from './request-signal-evidence.mjs';
test('explicit fetch init carries original incoming signal; parent abort proof is outside child capability', async () => {
  const controller = new AbortController();
  const id = '11111111-1111-4111-8111-111111111111';
  const request = new Request('http://probe.invalid/', {method: 'POST', signal: controller.signal,
    body: JSON.stringify({id, mode: 'cpu_abort'})});
  const childCapability = {};
  let receivedSignal;
  const loader = {load(code) {
    assert.deepEqual(code.env, {EFFECTS: childCapability});
    assert.equal(code.globalOutbound, null);
    return {getEntrypoint() {return {async fetch(url, init) {
      assert.equal(url, 'https://attempt.invalid/');
      receivedSignal = init.signal;
      return Response.json({synthetic: true});
    }};}};
  }};
  const aborts = [];
  const pending = [];
  await forwardSignalProbe(request, loader, () => childCapability, async value => {aborts.push(value);}, promise => pending.push(promise));
  assert.equal(receivedSignal, request.signal);
  controller.abort();
  await Promise.all(pending);
  assert.deepEqual(aborts, [id]);
  assert.equal(receivedSignal.aborted, true);
});
test('unsupported probe cannot create a child or effect capability', async () => {
  const request = new Request('http://probe.invalid/', {method: 'POST', body: JSON.stringify({id: 'wrong', mode: 'unknown'})});
  const result = await forwardSignalProbe(request, {load() {assert.fail();}}, () => assert.fail(), () => assert.fail(), () => assert.fail());
  assert.equal(result.status, 422);
});
test('external abort without a parent receipt does not qualify incoming cancellation', () => {
  const events = [{event: 'before', elapsed_ms: 0}, {event: 'abort_requested', elapsed_ms: 10}, {event: 'after', elapsed_ms: 100}];
  assert.deepEqual(classifySignalRecord({mode: 'cpu_abort', events}), {
    classification: 'incoming_abort_not_observed', incoming_abort_observed: false, physical_termination_verified: false});
});
test('late parent receipt is distinct from a subsequent effect; neither certifies stop', () => {
  const events = [{event: 'abort_requested', elapsed_ms: 10}, {event: 'parent_abort_received', elapsed_ms: 20}, {event: 'after', elapsed_ms: 30}];
  assert.equal(classifySignalRecord({mode: 'cpu_abort', events}).classification, 'effect_after_parent_abort');
  assert.equal(classifySignalRecord({mode: 'cpu_abort', events: [events[0], {...events[2], elapsed_ms: 20}, {...events[1], elapsed_ms: 30}]}).classification,
    'late_parent_abort_observation');
  const absent = classifySignalRecord({mode: 'cpu_abort', events: events.slice(0, 2)});
  assert.equal(absent.classification, 'physical_stop_unproven');
  assert.equal(absent.physical_termination_verified, false);
});
test('malformed observer clocks cannot produce a cancellation conclusion', () => {
  assert.throws(() => classifySignalRecord({mode: 'cpu_abort', events: [{event: 'abort_requested', elapsed_ms: NaN}]}), TypeError);
});
