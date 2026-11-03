import test from 'node:test';
import assert from 'node:assert/strict';
import {cpuProbeInput, handleCpuProbe} from './policy.mjs';

const token = 'synthetic-run-token';
const body = {case_id: 'entrypoint_limit', run_token: token};
function request(value = body, auth = 'synthetic-guard') {
  return new Request('https://probe.invalid/', {method: 'POST', headers: {'X-Dev-Auth': auth}, body: JSON.stringify(value)});
}
function environment() {
  return {DEV_AUTH: 'synthetic-guard', DEV_EXPIRES_AT: String(Date.now() + 60000),
    LOADER: {load() { assert.fail('Must reject before creating child'); }},
    DEV_API: {fetch() { assert.fail('Must reject before API effects'); }}};
}

test('strict fixed-case input rejects code, endpoint overrides, canonical dispatch and header injection', () => {
  assert.deepEqual(cpuProbeInput(body), {caseId: 'entrypoint_limit', token});
  for (const value of [null, [], {...body, code: 'pass'}, {...body, mode: 'canonical'}, {...body, api_url: 'https://attacker.invalid'},
    {...body, run_token: token + '\r\nInjected: x'}, {...body, case_id: 'unknown'}, {...body, run_token: 'x'.repeat(4097)}]) {
    assert.throws(() => cpuProbeInput(value), TypeError);
  }
});

test('expired, absent and invalid parent guard cause no observer or child effects', async () => {
  for (const overrides of [{DEV_AUTH: ''}, {DEV_EXPIRES_AT: '0'}, {DEV_EXPIRES_AT: 'NaN'}, {}]) {
    const env = {...environment(), ...overrides};
    const result = await handleCpuProbe(request(body, 'wrong'), env, () => assert.fail('No capability'));
    assert.equal(result.status, 403);
  }
  const result = await handleCpuProbe(request(), {...environment(), DEV_EXPIRES_AT: '0'}, () => assert.fail());
  assert.equal(result.status, 403);
});

test('unknown fields and oversized payloads are refused before API or loader', async () => {
  for (const value of [{...body, mode: 'canonical'}, {...body, run_token: 'x'.repeat(9000)}]) {
    assert.equal((await handleCpuProbe(request(value), environment(), () => assert.fail())).status, 422);
  }
});

test('invalid runtime identity cannot start a billable child', async () => {
  const env = environment();
  env.DEV_API = {async fetch(input) {
    assert.equal(input.redirect, 'manual');
    assert.equal(input.headers.get('Authorization'), `Bearer ${token}`);
    assert.equal((await input.json()).message, 'cpu-placement:entrypoint_limit:parent-preflight');
    return new Response('sensitive API denial detail', {status: 401});
  }};
  const response = await handleCpuProbe(request(), env, () => assert.fail('No child capability'));
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), {phase: 'observer-auth', observer_status: 401});
});

test('authorized parent keeps token in effect capability and uses exact entrypoint limit', async () => {
  const env = environment();
  const effects = {};
  let loaded = 0;
  env.DEV_API = {async fetch() {return Response.json({entry_id: 'synthetic'});}};
  env.LOADER = {load(code) {
    loaded++;
    assert.deepEqual(code.env, {EFFECTS: effects});
    assert.equal(code.globalOutbound, null);
    return {getEntrypoint(name, options) {
      assert.equal(name, null);
      assert.deepEqual(options, {limits: {cpuMs: 1000, subRequests: 4}});
      return {async fetch() {return Response.json({caught: 'CpuLimitExceeded'});}};
    }};
  }};
  const response = await handleCpuProbe(request(), env, input => {assert.deepEqual(input, {caseId: 'entrypoint_limit', token}); return effects;});
  assert.equal(response.status, 200);
  assert.equal(loaded, 1);
  const output = await response.text();
  assert.ok(!output.includes(token));
  assert.equal(JSON.parse(output).physical_termination_verified, false);
});
