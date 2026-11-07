import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {CPU_CASES, runCpuPlacement, recordCpuEffect, classifyCpuPlacement} from './cpu-placement.mjs';

test('each fixed case loads a fresh worker with unchanged budget and no network or credentials', async () => {
  const loaded = [];
  const invocations = [];
  const effects = {};
  const loader = {load(code) {
    loaded.push(code);
    return {getEntrypoint(...args) { return {async fetch(request) {
      invocations.push({args, body: await request.json()});
      return Response.json({completed: true});
    }}; }};
  }};
  for (const name of CPU_CASES) await runCpuPlacement(loader, effects, name);
  assert.equal(loaded.length, 3);
  for (const code of loaded) {
    assert.equal(code.globalOutbound, null);
    assert.deepEqual(Object.keys(code.env), ['EFFECTS']);
    assert.equal(code.env.EFFECTS, effects);
    assert.deepEqual(code.limits, {cpuMs: 1000, subRequests: 4});
  }
  assert.deepEqual(invocations.map(value => value.body.action), ['io', 'cpu_catch', 'cpu_catch']);
  assert.deepEqual(invocations.map(value => value.args), [[], [], [null, {limits: {cpuMs: 1000, subRequests: 4}}]]);
});

test('unsupported cases are rejected before loading or effects', async () => {
  await assert.rejects(runCpuPlacement({load() {assert.fail('must not load');}}, {}, 'arbitrary'), TypeError);
});

test('observer sends only fixed tags through the existing run-authenticated log route', async () => {
  const requests = [];
  const api = {async fetch(request) { requests.push(request); return Response.json({entry_id: 'synthetic'}); }};
  await recordCpuEffect(api, 'synthetic-token', 'entrypoint_limit', 'caught');
  const request = requests[0];
  assert.equal(request.url, 'https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev/api/sdk/runtime/logs');
  assert.equal(request.redirect, 'manual');
  assert.equal(request.headers.get('Authorization'), 'Bearer synthetic-token');
  assert.deepEqual(await request.json(), {level: 'INFO', message: 'cpu-placement:entrypoint_limit:caught'});
  await assert.rejects(recordCpuEffect(api, 'synthetic-token', 'entrypoint_limit', 'secret-text'), TypeError);
  assert.equal(requests.length, 1);
});

test('rejected observer writes fail closed without exposing response or token', async () => {
  const api = {async fetch() { return new Response('sensitive-provider-text', {status: 401}); }};
  await assert.rejects(recordCpuEffect(api, 'synthetic-token', 'code_limit', 'before'),
    {message: 'Independent observer rejected event'});
});

test('absence of effects and successful control cannot certify physical stop', () => {
  assert.equal(classifyCpuPlacement('completion', ['before', 'after']).classification, 'observer_control_passed');
  for (const events of [[], ['before'], ['before', 'caught']]) {
    const result = classifyCpuPlacement('entrypoint_limit', events);
    assert.equal(result.classification, 'physical_stop_unproven');
    assert.equal(result.physical_termination_verified, false);
    assert.equal(result.execution_admission_enabled, false);
  }
});

test('only ordered independent caught/after events identify continuation', () => {
  assert.equal(classifyCpuPlacement('code_limit', ['before', 'caught', 'after']).classification, 'effect_after_caught_exception');
  assert.equal(classifyCpuPlacement('code_limit', ['after', 'caught']).classification, 'physical_stop_unproven');
  assert.throws(() => classifyCpuPlacement('code_limit', ['unknown']), TypeError);
});

test('exception outcome preserves only error type, never exception text', async () => {
  const loader = {load() { return {getEntrypoint() { return {fetch() { throw new Error('synthetic-secret'); }}; }}; }};
  assert.deepEqual(await runCpuPlacement(loader, {}, 'code_limit'), {case_id: 'code_limit', error_type: 'Error'});
});

test('native driver refuses accidental invocation before binding or fetching', () => {
  const environment = {...process.env};
  delete environment.CPU_PLACEMENT_LOCAL;
  const result = spawnSync(process.execPath, [new URL('./cpu-placement-driver.mjs', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')],
    {env: environment, encoding: 'utf8', timeout: 3000});
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Explicit isolated local qualification required/);
});
