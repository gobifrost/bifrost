// Executor egress-gate contract: per-attempt declared HTTPS hosts are
// allowlisted BEFORE any loader/network/SDK effect. Absent egress keeps old
// behavior (test-api.invalid only). Invalid shape fails closed as 422
// egress-rejected with zero api_calls and without echoing secrets/values;
// only the offending declaration shape class is named. Run with:
// node --test egress-allowlist.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import {egressRejection, isHostAllowed, isUrlAllowed} from './egress-allowlist.mjs';

const envelope = (rejection) => Response.json({phase: 'egress-rejected',
  reason: rejection.reason, api_calls: []}, {status: 422});

test('absent egress keeps old behavior unchanged', () => {
  assert.equal(egressRejection(undefined), null);
  assert.equal(egressRejection({}), null);
  assert.equal(egressRejection({hosts: undefined}), null);
  assert.equal(isHostAllowed('test-api.invalid', undefined), true);
  assert.equal(isHostAllowed('TEST-API.INVALID', undefined), true);
  assert.equal(isHostAllowed('allowed.example', undefined), false);
  assert.equal(isUrlAllowed('https://test-api.invalid/api/sdk/config/get', undefined), true);
  assert.equal(isUrlAllowed('https://allowed.example/write', undefined), false);
  assert.equal(isUrlAllowed('https://allowed.example/write', {}), false);
});

test('admit listed hosts passes with no rejection', () => {
  assert.equal(egressRejection({hosts: []}), null);
  assert.equal(egressRejection({hosts: ['allowed.example']}), null);
  assert.equal(egressRejection({hosts: ['Allowed.Example', 'second.example']}), null);
  assert.equal(isHostAllowed('allowed.example', {hosts: ['allowed.example']}), true);
  assert.equal(isHostAllowed('ALLOWED.EXAMPLE', {hosts: ['allowed.example']}), true);
  assert.equal(isHostAllowed('allowed.example', {hosts: ['Allowed.Example']}), true);
  assert.equal(isHostAllowed('test-api.invalid', {hosts: ['allowed.example']}), true);
  assert.equal(isUrlAllowed('https://allowed.example/write', {hosts: ['allowed.example']}), true);
  assert.equal(isUrlAllowed('https://ALLOWED.example/write?x=1', {hosts: ['allowed.example']}), true);
  assert.equal(isUrlAllowed('https://test-api.invalid/api/sdk/config/get', {hosts: ['allowed.example']}), true);
});

test('reject unlisted host with zero api_calls', async () => {
  const secret = 'Bearer S3CR3T-engine-token-xyz';
  const params = {n: 7, secret_param: 'param-S3CR3T'};
  const egress = {hosts: ['allowed.example']};
  assert.equal(egressRejection(egress), null);
  assert.equal(isHostAllowed('unlisted.example', egress), false);
  assert.equal(isUrlAllowed('https://unlisted.example/write', egress), false);
  assert.equal(isUrlAllowed('https://unlisted.example/write', undefined), false);
  // Shape-valid declaration, fetch-time unlisted -> 422 egress-rejected, no values.
  const rejection = {reason: 'unlisted'};
  const response = Response.json({phase: 'egress-rejected', reason: rejection.reason, api_calls: []}, {status: 422});
  assert.equal(response.status, 422);
  const body = await response.json();
  assert.equal(body.phase, 'egress-rejected');
  assert.equal(body.reason, 'unlisted');
  assert.deepEqual(body.api_calls, []);
  const encoded = JSON.stringify(body);
  assert.ok(!encoded.includes('S3CR3T'), 'envelope must not carry secrets');
  assert.ok(!encoded.includes('secret_param'), 'envelope must not carry params');
  assert.ok(!encoded.includes(secret), 'envelope must not carry engine token');
  assert.ok(!encoded.includes('unlisted.example'), 'envelope must not echo host values');
  assert.ok(!encoded.includes('allowed.example'), 'envelope must not echo allowlist values');
  void params;
});

test('invalid egress shape fails closed without echo', async () => {
  const badCases = [
    [{hosts: 'x'}, 'hosts'],
    [{hosts: [42]}, 'hostname'],
    [{hosts: ['']}, 'hostname'],
    [{hosts: ['a'.repeat(254)]}, 'hostname'],
    [{hosts: Array(9).fill('a.example')}, 'hosts'],
    [{hosts: ['evil.example:443']}, 'hostname'],
    [{hosts: ['user:pass@evil.example']}, 'hostname'],
    [{hosts: ['evil.example/path']}, 'hostname'],
    [{hosts: ['evil.example?x=1']}, 'hostname'],
    [{hosts: ['evil.example#frag']}, 'hostname'],
    [{hosts: ['192.168.0.1']}, 'hostname'],
    [{hosts: ['[::1]']}, 'hostname'],
    [{hosts: ['UPPER..DOTS..example']}, 'hostname'],
    [{hosts: ['trailing.example.']}, 'hostname'],
    ['egress', 'egress'],
    [null, 'egress'],
    [[], 'egress'],
    [{hosts: ['ok.example', 42]}, 'hostname'],
  ];
  for (const [bad, shapeClass] of badCases) {
    const rejection = egressRejection(bad);
    assert.ok(rejection !== null, `expected rejection for ${JSON.stringify(bad)?.slice(0, 60)}`);
    assert.equal(rejection.reason, shapeClass);
    assert.deepEqual(Object.keys(rejection), ['reason']);
    const response = envelope(rejection);
    assert.equal(response.status, 422);
    const body = await response.json();
    assert.equal(body.phase, 'egress-rejected');
    assert.equal(body.reason, shapeClass);
    assert.deepEqual(body.api_calls, []);
    const encoded = JSON.stringify(body);
    assert.ok(!encoded.includes('S3CR3T'), 'envelope must not carry secrets');
    // Must never echo declared values, only shape class.
    const serializedBad = JSON.stringify(bad);
    if (serializedBad && serializedBad.length < 200) {
      for (const token of ['evil.example', '192.168', 'S3CR3T']) {
        if (serializedBad.includes(token)) assert.ok(!encoded.includes(token), `must not echo ${token}`);
      }
    }
  }
});

test('no secret echo in shape rejection envelope', async () => {
  const secret = 'Bearer S3CR3T-engine-token-xyz';
  const rejection = egressRejection({hosts: ['evil.example:443']});
  assert.ok(rejection !== null);
  assert.equal(rejection.reason, 'hostname');
  const response = envelope(rejection);
  const body = await response.json();
  assert.deepEqual(body.api_calls, []);
  const encoded = JSON.stringify(body);
  assert.ok(!encoded.includes('S3CR3T'));
  assert.ok(!encoded.includes(secret));
  assert.ok(!encoded.includes('evil.example'));
});
