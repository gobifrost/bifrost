import test from 'node:test';
import assert from 'node:assert/strict';
import {handleSignalProbe} from './policy.mjs';
const token = 'synthetic-run-token';
function request(body, signal) {return new Request('https://probe.invalid/', {method: 'POST', signal,
  headers: {'X-Dev-Auth': 'guard'}, body: JSON.stringify(body)});}
function env() {return {DEV_AUTH: 'guard', DEV_EXPIRES_AT: String(Date.now() + 60000),
  LOADER: {load() {assert.fail('No Python child allowed');}}, DEV_API: {fetch() {assert.fail('No API effects allowed');}}};}
test('expired guard and unsupported canonical input reject before effects', async () => {
  assert.equal((await handleSignalProbe(request({}), {...env(), DEV_EXPIRES_AT: '0'}, () => assert.fail(), () => assert.fail())).status, 403);
  assert.equal((await handleSignalProbe(request({mode: 'canonical'}), env(), () => assert.fail(), () => assert.fail())).status, 422);
});
test('invalid runtime identity cannot create a child', async () => {
  const environment = env();
  environment.DEV_API = {async fetch() {return new Response('', {status: 401});}};
  const response = await handleSignalProbe(request({case_id: 'io_abort', run_token: token}), environment, () => assert.fail(), () => assert.fail());
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), {phase: 'observer-auth', observer_status: 401});
});
test('parent completion control records effects without loading Python', async () => {
  const messages = [];
  const environment = env();
  environment.DEV_API = {async fetch(input) {messages.push((await input.json()).message); return Response.json({entry_id: 'synthetic'});}};
  const response = await handleSignalProbe(request({case_id: 'completion', run_token: token}), environment, () => assert.fail(), () => assert.fail());
  assert.equal(response.status, 200);
  assert.deepEqual(messages, ['signal:completion:preflight', 'signal:completion:before', 'signal:completion:after']);
});
