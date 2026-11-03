import assert from 'node:assert/strict';
import test from 'node:test';
import {createAttemptEgress} from './attempt-egress.mjs';

test('expired capability rejects each new request without relying on a timer', async () => {
  let time = 9, forwarded = 0;
  const gate = createAttemptEgress(10, async () => {forwarded++; return new Response('ok');}, () => time);
  assert.equal(await (await gate.fetch(new Request('https://api.invalid/'))).text(), 'ok');
  time = 10; // No timer callback or cooperative child cancellation runs.
  for (let i = 0; i < 3; i++) {
    assert.equal((await gate.fetch(new Request('https://api.invalid/'))).status, 410);
  }
  time = 1; // An expired capability cannot reopen if the clock regresses.
  assert.equal((await gate.fetch(new Request('https://api.invalid/'))).status, 410);
  assert.equal(forwarded, 1);
});

test('closing one attempt does not close a separate attempt', async () => {
  const forward = async () => new Response('ok');
  const first = createAttemptEgress(10, forward, () => 1);
  const second = createAttemptEgress(10, forward, () => 1);
  first.close(); first.close();
  assert.equal((await first.fetch(new Request('https://api.invalid/'))).status, 410);
  assert.equal((await second.fetch(new Request('https://api.invalid/'))).status, 200);
});

test('dispatch before expiry can commit after close: preserve the uncertain outcome', async () => {
  let release, writes = 0, calls = 0;
  const blocked = new Promise(resolve => {release = resolve;});
  const gate = createAttemptEgress(10, async () => {
    calls++; await blocked; writes++; throw new Error('response lost after commit');
  }, () => 1);
  const result = gate.fetch(new Request('https://api.invalid/', {method: 'POST'}));
  gate.close();
  assert.equal((await gate.fetch(new Request('https://api.invalid/', {method: 'POST'}))).status, 410);
  const rejection = assert.rejects(result, /response lost after commit/);
  release(); await rejection;
  assert.equal(calls, 1); assert.equal(writes, 1); // No retry or invented rollback.
});

test('invalid deadline/clock fail closed before transport', async () => {
  const forbidden = () => assert.fail('Must not dispatch');
  assert.throws(() => createAttemptEgress(NaN, forbidden), TypeError);
  for (const time of [NaN, Infinity]) {
    const gate = createAttemptEgress(10, forbidden, () => time);
    assert.equal((await gate.fetch(new Request('https://api.invalid/'))).status, 410);
  }
});
