import assert from 'node:assert/strict';
import test from 'node:test';
import {invocationStream} from './invocation-stream.mjs';
const identity = {runtimeHandle: 'invocation-1', executionId: 'run-1'};
const decode = bytes => JSON.parse(new TextDecoder().decode(bytes));

test('accepted frame precedes required work completion; terminal frame closes stream and cleans once', async () => {
  let release, cleaned = 0;
  const required = new Promise(resolve => {release = resolve;});
  const response = invocationStream({...identity, execute: () => required, cleanup: () => cleaned++});
  const reader = response.body.getReader();
  assert.equal(decode((await reader.read()).value).type, 'accepted');
  assert.equal(cleaned, 0);
  release({result: {success: true}});
  assert.equal(decode((await reader.read()).value).type, 'result');
  assert.equal((await reader.read()).done, true);
  assert.equal(cleaned, 1);
});

test('cancellation closes capability without pretending required work stopped', async () => {
  let release, finish, enter, cleaned = 0, continued = false;
  const entered = new Promise(resolve => {enter = resolve;});
  const blocked = new Promise(resolve => {release = resolve;});
  const settled = new Promise(resolve => {finish = resolve;});
  const response = invocationStream({...identity, execute: async () => {enter(); await blocked; continued = true; finish(); return {};}, cleanup: () => cleaned++});
  const reader = response.body.getReader();
  await reader.read();
  await entered;
  await reader.cancel();
  assert.equal(cleaned, 1);
  release(); await settled;
  assert.equal(continued, true);
  assert.equal(cleaned, 1);
});

test('provider errors yield bounded non-secret collection errors', async () => {
  for (const execute of [async () => {const error = new Error('synthetic-secret'); error.name = 'synthetic-secret'; throw error;}]) {
    let cleaned = 0;
    const response = invocationStream({...identity, execute, cleanup: () => cleaned++});
    const lines = (await response.text()).trim().split('\n').map(JSON.parse);
    assert.equal(lines.length, 2);
    assert.equal(lines[1].type, 'error');
    assert.ok(!JSON.stringify(lines).includes('synthetic-secret'));
    assert.equal(cleaned, 1);
  }
});
