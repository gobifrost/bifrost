// Pure unit (fake LOADER, no workerd): per-tenant serialization, queue wait
// accounting, and gates firing BEFORE any loader effect. Run attempts are
// PATH-keyed: body.modules maps storage paths to source, body.entry is a
// storage path, and every child byte comes out of the TenantGateway after
// manifest-pin verification (never the raw store).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import entry, {enqueueTenant, resetTenantState} from './tenant-entry.mjs';
import {genericCodeDigest} from './generic-worker-code.mjs';

const WF = 'workflows/wf.py';
const GHOST = 'workflows/ghost.py';
const HELPER = 'workflows/helper.py';

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

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

test('capability gate rejects BEFORE loader get AND before any module fetch', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'run',
    modules: {[WF]: 'X=1\n'}, entry: WF,
    requirements: {modules: [WF, GHOST]},
    manifest: {[WF]: await sha256Hex('X=1\n')}}), fakeEnv(log), {});
  assert.equal(res.status, 422);
  const body = await res.json();
  assert.deepEqual(body.missing_modules, [GHOST]);
  assert.deepEqual(body.api_calls, [], 'no gateway module fetch may happen on gate reject');
  assert.deepEqual(log.getCalls, [], 'loader must not be touched on gate reject');
});

test('non-path entry is rejected before any effect', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'run',
    modules: {wf: 'X=1'}, entry: 'wf',
    requirements: {modules: ['wf']}, manifest: {wf: 'hash'}}), fakeEnv(log), {});
  assert.equal(res.status, 400);
  assert.deepEqual(log.getCalls, []);
});

test('egress gate rejects BEFORE loader get (no worker effect)', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'honest',
    url: 'https://evil.example/collect', egress: {hosts: ['example.com']}}), fakeEnv(log), {});
  assert.equal(res.status, 422);
  assert.deepEqual(log.getCalls, [], 'loader must not be touched on gate reject');
});

test('undeclared STORAGE PATH import maps to terminal AFTER run (post-hoc, like executor)', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const child = {getEntrypoint: () => ({fetch: async () =>
    Response.json({result: {ok: true}, recorded: [WF, HELPER]})})};
  const env = {LOCAL_PROBE: 'true', LOADER: {get: (id, f) => { log.getCalls.push(id); f(); return child; }}};
  const wfSrc = 'X = 1\n';
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'run',
    modules: {[WF]: wfSrc, [HELPER]: wfSrc}, entry: WF,
    requirements: {modules: [WF]},
    manifest: {[WF]: await sha256Hex(wfSrc), [HELPER]: await sha256Hex(wfSrc)}}), env, {});
  assert.equal(res.status, 502);
  const body = await res.json();
  assert.equal(body.phase, 'undeclared-import');
  assert.deepEqual(body.provider_outcome.undeclared_modules, [HELPER]);
  assert.ok(body.api_calls.some((c) => c.path === `/api/sdk/modules/${HELPER}` && c.status === 200),
    `gateway fetched the undeclared-but-loadable path: ${JSON.stringify(body.api_calls)}`);
  assert.equal(log.getCalls.length, 1, 'run executed, then mapped post-hoc');
});

test('entry that the gateway cannot resolve fails BEFORE the loader effect', async () => {
  resetTenantState();
  const log = {getCalls: [], fetchCalls: []};
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'run',
    modules: {}, entry: WF,
    requirements: {modules: [WF]}, manifest: {[WF]: await sha256Hex('X = 1\n')}}), fakeEnv(log), {});
  assert.equal(res.status, 502);
  assert.equal((await res.json()).phase, 'module-preflight');
  assert.deepEqual(log.getCalls, [], 'unresolvable entry must never reach the loader');
});

test('declared path run succeeds with gateway api_calls and path-keyed child body', async () => {
  resetTenantState();
  let seen;
  const child = {getEntrypoint: () => ({fetch: async (req) => {
    seen = await req.json();
    return Response.json({result: {ok: true}, recorded: [WF]});
  }})};
  const env = {LOCAL_PROBE: 'true', LOADER: {get: () => child}};
  const wfSrc = 'X = 1\n';
  const res = await entry.fetch(post({tenant_id: 'org-x', action: 'run',
    modules: {[WF]: wfSrc}, entry: WF,
    requirements: {modules: [WF]}, manifest: {[WF]: await sha256Hex(wfSrc)}}), env, {});
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.output.result.ok, true);
  assert.deepEqual(Object.keys(seen.modules), [WF]);
  assert.equal(seen.entry, WF);
  assert.ok(body.api_calls.some((c) => c.path === `/api/sdk/modules/${WF}` && c.status === 200));
  assert.ok(body.api_calls.some((c) => c.path.startsWith('/api/sdk/modules-resolve')));
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

test('legacy_load uses LOADER.load with no id (per-attempt meter control)', async () => {
  resetTenantState();
  const seen = {get: 0, load: 0, bodies: []};
  const child = {getEntrypoint: () => ({fetch: async (request) => {
    seen.bodies.push(await request.json());
    return Response.json({clean: {builtins: null}});
  }})};
  const env = {LOCAL_PROBE: 'true', LOADER: {
    get: () => { seen.get++; return child; },
    load: () => { seen.load++; return child; },
  }};
  const res = await entry.fetch(post({tenant_id: 'org-meter', action: 'legacy_load'}), env, {});
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.equal(body.mode, 'legacy_load');
  assert.equal(seen.load, 1, 'per-attempt control must call LOADER.load');
  assert.equal(seen.get, 0, 'per-attempt control must not use a stable tenant id');
  assert.equal(seen.bodies[0].credential, 'gateway-injected');
});
