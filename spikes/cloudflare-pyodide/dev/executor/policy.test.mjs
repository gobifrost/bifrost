import assert from 'node:assert/strict';
import test from 'node:test';
import {apiTarget, attemptPayload} from './policy.mjs';
test('runtime API capability retains a fixed destination and query', () => {
  assert.equal(apiTarget('https://test-api.invalid/api/sdk/modules/a.py?hash=1', 'https://dev.invalid').href,
    'https://dev.invalid/api/sdk/modules/a.py?hash=1');
});
test('supervisor public metadata and SDK transport cannot be selected by the request', () => {
  const body = {execution_id: 'run', context: {parameters: {n: 7}},
    api_url: 'https://attacker.invalid', public_url: 'https://attacker.invalid'};
  assert.deepEqual(attemptPayload(body, 'https://platform.invalid/'), {
    action: 'execute', execution_id: 'run', context: body.context,
    api_url: 'https://test-api.invalid', public_url: 'https://platform.invalid',
  });
});
test('invalid trusted metadata is rejected before runtime construction', () => {
  for (const url of ['http://platform.invalid', 'https://user:secret@platform.invalid',
    'https://platform.invalid/path', 'https://platform.invalid?token=secret', 'https://platform.invalid/#x']) {
    assert.throws(() => attemptPayload({}, url), TypeError);
  }
});
test('agent run control plane rides the same authenticated bridge', () => {
  assert.equal(apiTarget('https://test-api.invalid/api/agent-runs/enqueue', 'https://dev.invalid').href,
    'https://dev.invalid/api/agent-runs/enqueue');
  assert.equal(apiTarget('https://test-api.invalid/api/agent-runs/0192dead-beef', 'https://dev.invalid').href,
    'https://dev.invalid/api/agent-runs/0192dead-beef');
});
test('external fetch, control endpoints and origin ambiguity are rejected before effects', () => {
  for (const url of ['https://other.invalid/api/sdk/config/get', 'https://test-api.invalid/auth/login',
    'https://test-api.invalid/api/workflows', 'https://test-api.invalid/api/tables-admin', 'https://test-api.invalid/api/agent-runs-other']) {
    assert.throws(() => apiTarget(url, 'https://dev.invalid'));
  }
  for (const origin of ['http://dev.invalid', 'https://user:password@dev.invalid', 'https://dev.invalid/path', 'https://dev.invalid?x=1']) {
    assert.throws(() => apiTarget('https://test-api.invalid/api/tables', origin));
  }
});
