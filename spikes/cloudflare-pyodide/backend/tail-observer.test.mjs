import assert from 'node:assert/strict';
import test from 'node:test';
import {summarizeTail} from './tail-observer.mjs';
test('tail projection excludes raw logs, headers, URLs and exception text', () => {
  const event = {outcome: 'ok', eventTimestamp: 123, logs: [{message: ['synthetic-secret']}],
    exceptions: [{message: 'synthetic-secret'}], cpuTime: 2};
  Object.defineProperty(event, 'event', {get() {assert.fail('Never inspect request data');}});
  const summary = summarizeTail([event]);
  assert.equal(summary.log_count, 1);
  assert.equal(summary.exception_count, 1);
  assert.equal(summary.raw_numeric_fields.cpuTime, 2);
  assert.equal(summary.raw_numeric_fields.cpuTimeMs, null);
  assert.ok(!JSON.stringify(summary).includes('synthetic-secret'));
});
test('missing numeric fields stay unknown; malformed metrics never become measurements', () => {
  const summary = summarizeTail([{outcome: 'canceled', cpuTime: NaN, cpuTimeMs: -1, wallTime: '5'}]);
  assert.deepEqual(summary.raw_numeric_fields, {cpuTime: null, cpuTimeMs: null, wallTime: null, wallTimeMs: null});
  assert.equal(summary.log_count, null);
});
test('ambiguous multiple producers and unknown outcomes fail closed', () => {
  for (const events of [[], [{outcome: 'ok'}, {outcome: 'ok'}], [{outcome: 'stopped-for-sure'}]]) {
    assert.throws(() => summarizeTail(events), TypeError);
  }
});
