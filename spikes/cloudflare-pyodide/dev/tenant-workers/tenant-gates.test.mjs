// Pure unit (fake LOADER, no workerd): per-tenant serialization, queue wait
// accounting, and gates firing BEFORE any loader effect.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import entry, {enqueueTenant, resetTenantState} from './tenant-entry.mjs';
import {genericCodeDigest} from './generic-worker-code.mjs';

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

function fakeChild(log, latencyMs = 0) {
  return {
    getEntrypoint: () => ({
      fetch: async () => {
        if (latencyMs) await delay(latencyMs);
        log.push(Date.now());
        return Response.json({clean: {}, recorded: []});
      },
    }),
  };
}

function fakeEnv(log, latencyMs = 0) {
  return {LOCAL_PROBE: 'true', LOADER: {get: (id, factory) => {
    log.getCalls.push(id);
    factory();
    return fakeChild(log.fetchCalls, latencyMs);
  }}};
}

const post = (body) => new Request('https://sup.invalid/', {method: 'POST', body: JSON.stringify(body)});

test('same-tenant attempts serialize; queue wait is recorded', async () => {
  resetTenantState();
  const order = [];
  const first = enqueueTenant('t1', async () => { order.push('a-start'); await delay(120); order.push('a-end'); return 'A'; });
  const second = enqueueTenant('t1', async () => { order.push('b-start'); await delay(10); order.push('b-end'); return 'B'; });
  const [ra, rb] = await Promise.all([first, second]);
  assert.deepEqual(order, ['a-start', 'a-end', 'b-start', 'b-end']);
  assert.equal(ra.result, 'A');
  assert.equal(rb.result, 'B');
  assert.ok(ra.queueWaitMs < 50, `first should not wait, got ${ra.queueWaitMs}`);
  assert.ok(rb.queueWaitMs >= 100, `second should wait ~120ms, got ${rb.queueWaitMs}`);
});

test('different tenants do not block each other', async () => {
  resetTenantState();
  const t0 = Date.now();
  const [ra, rb] = await Promise.all([
    enqueueTenant('tA', async () => { await delay(120); return 'A'; }),
    enqueueTenant('tB', async () => { await delay(120); return 'B'; }),
  ]);
  const wall = Date.now() - t0;
  assert.ok(wall < 200, `parallel tenants should overlap, took ${wall}ms`);
  assert.ok(ra.queueWaitMs < 50 && rb.queueWaitMs < 50);
});

test('a failing attempt does not break the chain', async () => {
  resetTenantState();
  const bad = enqueueTenant('t1', async () => { throw new Error('boom'); });
  const good = enqueueTenant('t1', async () => 'recovered');
  await assert.rejects(() => bad, /boom/);
  assert.equal((await good).result, 'recovered');
});

test('capability gate rejects BEFORE loader get (no worker effect)', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'run',
    modules: {wf: 'X=1'}, entry: 'wf',
    requirements: {modules: ['wf', 'ghost-module']},
    manifest: {wf: 'hash'}}), fakeEnv(log), {});
  assert.equal(res.status, 422);
  assert.deepEqual((await res.json()).missing_modules, ['ghost-module']);
  assert.deepEqual(log.getCalls, [], 'loader must not be touched on gate reject');
});

test('egress gate rejects BEFORE loader get (no worker effect)', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'honest',
    url: 'https://evil.example/collect', egress: {hosts: ['example.com']}}), fakeEnv(log), {});
  assert.equal(res.status, 422);
  assert.deepEqual(log.getCalls, [], 'loader must not be touched on gate reject');
});

test('undeclared import maps to terminal AFTER run (post-hoc, like executor)', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const child = {getEntrypoint: () => ({fetch: async () =>
    Response.json({result: {ok: true}, recorded: ['wf', 'helper']})})};
  const env = {LOCAL_PROBE: 'true', LOADER: {get: (id, f) => { log.getCalls.push(id); f(); return child; }}};
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'run',
    modules: {wf: 'import helper', helper: 'X=1'}, entry: 'wf',
    requirements: {modules: ['wf']}, manifest: {wf: 'h', helper: 'h'}}), env, {});
  assert.equal(res.status, 502);
  const body = await res.json();
  assert.equal(body.phase, 'undeclared-import');
  assert.deepEqual(body.provider_outcome.undeclared_modules, ['helper']);
  assert.equal(log.getCalls.length, 1, 'run executed, then mapped post-hoc');
});

test('success envelope carries tenant id, reuse flag and queue wait', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const env = fakeEnv(log);
  const first = await (await entry.fetch(post({tenant_id: 'org-x', action: 'honest'}), env, {})).json();
  assert.equal(first.worker_reused, false);
  assert.ok(typeof first.queue_wait_ms === 'number');
  const second = await (await entry.fetch(post({tenant_id: 'org-x', action: 'honest'}), env, {})).json();
  assert.equal(second.worker_reused, true);
  assert.equal(first.tenant_worker_id, second.tenant_worker_id);
});

test('child request carries only the dummy placeholder, never a secret', async () => {
  resetTenantState();
  let seen;
  const child = {getEntrypoint: () => ({fetch: async (req) => {
    seen = await req.json(); return Response.json({recorded: []});
  }})};
  const env = {LOCAL_PROBE: 'true', LOADER: {get: () => child}};
  await entry.fetch(post({tenant_id: 'org-x', action: 'honest'}), env, {});
  assert.equal(seen.credential, 'gateway-injected');
  assert.ok(!JSON.stringify(seen).match(/eyJ[A-Za-z0-9_-]{10,}/), 'no JWT may ride the child request');
});

test('/__code_digest exposes the constant script hash', async () => {
  const res = await entry.fetch(new Request('https://sup.invalid/__code_digest'),
    {LOCAL_PROBE: 'true'}, {});
  assert.equal((await res.json()).generic_code_sha256, await genericCodeDigest());
});
