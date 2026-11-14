import assert from 'node:assert/strict';
import test from 'node:test';
import {createRefreshGate} from './refresh-gate.mjs';
test('response remains held after readiness until explicit release',async()=>{
  const gate=createRefreshGate();let forwarded=false;
  const held=gate.holdResponse().then(()=>{forwarded=true;});
  await gate.waitReady();assert.equal(forwarded,false);
  gate.release();await held;
  assert.deepEqual(gate.events,['server_response_held','ready_observed','release_requested','response_forwarded']);
});
test('cleanup release is idempotent and can precede a late response',async()=>{
  const gate=createRefreshGate();gate.release();gate.release();await gate.holdResponse();
  assert.deepEqual(gate.events,['release_requested','server_response_held','response_forwarded']);
});
test('cleanup closes readiness when no successful response arrives',async()=>{
  const gate=createRefreshGate();const ready=gate.waitReady();
  gate.close();gate.close();assert.equal(await ready,false);
  await gate.holdResponse();
  assert.deepEqual(gate.events,['release_requested','server_response_held','response_forwarded']);
});
