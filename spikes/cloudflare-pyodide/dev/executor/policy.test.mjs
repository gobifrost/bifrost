import assert from 'node:assert/strict';
import test from 'node:test';
import {apiTarget} from './policy.mjs';
test('runtime API capability retains a fixed destination and query', () => {
  assert.equal(apiTarget('https://test-api.invalid/api/sdk/modules/a.py?hash=1', 'https://dev.invalid').href,
    'https://dev.invalid/api/sdk/modules/a.py?hash=1');
});
test('external fetch, control endpoints and origin ambiguity are rejected before effects', () => {
  for (const url of ['https://other.invalid/api/sdk/config/get', 'https://test-api.invalid/auth/login',
    'https://test-api.invalid/api/workflows', 'https://test-api.invalid/api/tables-admin']) {
    assert.throws(() => apiTarget(url, 'https://dev.invalid'));
  }
  for (const origin of ['http://dev.invalid', 'https://user:password@dev.invalid', 'https://dev.invalid/path', 'https://dev.invalid?x=1']) {
    assert.throws(() => apiTarget('https://test-api.invalid/api/tables', origin));
  }
});
