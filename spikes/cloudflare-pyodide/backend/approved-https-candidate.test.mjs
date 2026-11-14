import assert from 'node:assert/strict';
import test from 'node:test';
import {createApprovedHttpsCandidate} from './approved-https-candidate.mjs';

const origin = 'https://vendor.example';
const request = (url = origin, options) => new Request(url, options);
const policy = options => createApprovedHttpsCandidate({deadline: 10, now: () => 1,
  maxRequests: 2, forward: () => assert.fail('Unexpected forward'), ...options});

test('empty policy and exact origins refuse undeclared destinations without spending budget', async () => {
  assert.equal((await policy().fetch(request())).status, 403);
  let calls = 0;
  const origins = [origin];
  const gate = policy({origins, maxRequests: 1, forward: async outbound => {
    calls++;
    assert.equal(outbound.redirect, 'manual');
    assert.equal(outbound.headers.get('authorization'), 'Bearer vendor-token');
    assert.equal(await outbound.text(), 'payload');
    return new Response('ok');
  }});
  origins.push('https://other.example');
  for (const url of ['https://other.example', 'https://vendor.example.attacker.example',
    'http://vendor.example', 'https://vendor.example:8443']) {
    assert.equal((await gate.fetch(request(url))).status, 403);
  }
  assert.equal(await (await gate.fetch(request(`${origin}/write`, {method: 'POST',
    headers: {authorization: 'Bearer vendor-token'}, body: 'payload'}))).text(), 'ok');
  assert.equal((await gate.fetch(request())).status, 429);
  assert.equal(calls, 1);
});

test('invalid operator configuration is rejected before transport', () => {
  for (const value of ['http://vendor.example', `${origin}/`, `${origin}/path`,
    `${origin}?query`, `${origin}#fragment`, 'https://user:pass@vendor.example',
    'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'https://*.example',
    'https://vendor.example.']) assert.throws(() => policy({origins: [value]}));
  assert.throws(() => policy({origins: [origin, origin]}));
  assert.throws(() => policy({origins: Array(17).fill(origin)}));
  for (const maxRequests of [0, 101, 1.5, undefined]) assert.throws(() => policy({maxRequests}));
  assert.throws(() => policy({forbiddenCredentials: ['']}));
  assert.throws(() => policy({forward: undefined}));
});

test('known run credentials in any header are refused and config is snapshotted', async () => {
  let calls = 0;
  const credentials = ['synthetic-run-token'];
  const gate = policy({origins: [origin], forbiddenCredentials: credentials,
    forward: async () => { calls++; return new Response('ok'); }});
  credentials.length = 0;
  for (const name of ['authorization', 'x-custom']) {
    assert.equal((await gate.fetch(request(origin, {
      headers: {[name]: 'prefix synthetic-run-token suffix'},
    }))).status, 403);
  }
  assert.equal(calls, 0);
});

test('deadline and explicit close refuse future dispatch, independently per attempt', async () => {
  let time = 1, calls = 0;
  const options = {origins: [origin], now: () => time,
    forward: async () => {calls++; return new Response('ok');}};
  const first = policy(options), second = policy(options);
  first.close();
  assert.equal((await first.fetch(request())).status, 410);
  assert.equal((await second.fetch(request())).status, 200);
  time = 10;
  assert.equal((await second.fetch(request())).status, 410);
  time = 1;
  assert.equal((await second.fetch(request())).status, 410);
  assert.equal(calls, 1);
});

test('response loss consumes budget and preserves original uncertainty without retry', async () => {
  let release, writes = 0;
  const blocked = new Promise(resolve => {release = resolve;});
  const error = new Error('synthetic response lost');
  const gate = policy({origins: [origin], maxRequests: 1, forward: async () => {
    await blocked; writes++; throw error;
  }});
  const pending = gate.fetch(request(origin, {method: 'POST'}));
  assert.equal((await gate.fetch(request())).status, 429);
  gate.close();
  const rejected = assert.rejects(pending, actual => actual === error);
  release(); await rejected;
  assert.equal(writes, 1);
});

test('redirect response is returned without gateway follow; next destination is checked', async () => {
  let calls = 0;
  const gate = policy({origins: [origin], forward: async outbound => {
    calls++; assert.equal(outbound.redirect, 'manual');
    return new Response(null, {status: 302, headers: {location: 'https://other.example/'}});
  }});
  const response = await gate.fetch(request());
  assert.equal(response.status, 302);
  assert.equal((await gate.fetch(request(response.headers.get('location')))).status, 403);
  assert.equal(calls, 1);
});
