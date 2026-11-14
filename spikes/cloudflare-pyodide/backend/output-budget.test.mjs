import assert from 'node:assert/strict';
import test from 'node:test';
import {invocationStream} from './invocation-stream.mjs';

test('a consumed oversized terminal yields an explicit bounded executor failure after work returns', async () => {
  let returned = false, cleaned = 0;
  const response = invocationStream({runtimeHandle: 'handle-1', executionId: 'run-1',
    execute: async () => {returned = true; return {runtime_handle: 'handle-1',
      result: {execution_id: 'run-1', success: true, result: 'sensitive-value'.repeat(30000)}};},
    cleanup: () => cleaned++});
  const text = await response.text();
  const frames = text.trim().split('\n').map(JSON.parse);
  assert.equal(returned, true);
  assert.equal(frames[1].type, 'result');
  assert.equal(frames[1].evidence.result.success, false);
  assert.equal(frames[1].evidence.result.error_type, 'ExecutorFrameTooLargeError');
  assert.equal(frames[1].evidence.provider_outcome.execute_returned, true);
  assert.equal(frames[1].evidence.provider_outcome.engine_outcome_delivered, false);
  assert.ok(new TextEncoder().encode(text).byteLength < 2048);
  assert.ok(!text.includes('sensitive-value'));
  assert.equal(cleaned, 1);
});

test('an execution error is not relabeled as a settled output-size failure', async () => {
  const response = invocationStream({runtimeHandle: 'handle-1', executionId: 'run-1',
    execute: async () => {throw new RangeError('sensitive-error');}, cleanup: () => {}});
  const text = await response.text();
  const frame = JSON.parse(text.trim().split('\n')[1]);
  assert.equal(frame.type, 'error');
  assert.equal(frame.error_type, 'RangeError');
  assert.ok(!text.includes('sensitive-error'));
});
