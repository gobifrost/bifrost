import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {logDeliveryEvidence} from './log-delivery-evidence.mjs';
const receipt = JSON.parse(readFileSync(new URL('engine-cooperative-cancel-v4.json', import.meta.url), 'utf8'));

test('actual cancellation receipt distinguishes captured logs from canonical persistence', () => {
  const attempt = Object.values(receipt.shared_pool_result.attempts).find(item => item.label === 'fails-first');
  const evidence = logDeliveryEvidence(attempt.evidence.result.logs, attempt.evidence.api_calls);
  assert.deepEqual(evidence, {kind: 'runtime_log_delivery_unconfirmed', unsuccessful_log_requests: 2,
    captured_log_entries: 4, persistence_verified: false, automatic_replay_safe: false});
  assert.equal(attempt.persisted_logs.length, 2);
  assert.ok(attempt.evidence.result.logs.some(row => row.message === 'engine_cooperative_cancel_suppressed'));
  assert.ok(!attempt.persisted_logs.includes('engine_cooperative_cancel_suppressed'));
});

test('accepted peer transport does not become a persistence claim', () => {
  const peer = Object.values(receipt.shared_pool_result.attempts).find(item => item.label === 'succeeds-second');
  assert.equal(logDeliveryEvidence(peer.evidence.result.logs, peer.evidence.api_calls), null);
});

test('projection excludes raw log text and unknown capture counts', () => {
  const evidence = logDeliveryEvidence(undefined, [{method: 'POST', path: '/api/sdk/runtime/logs',
    status: 401, authorization: 'synthetic-sensitive-token', body: 'sensitive-log'}]);
  assert.equal(evidence.captured_log_entries, null);
  assert.ok(!JSON.stringify(evidence).includes('sensitive'));
});
