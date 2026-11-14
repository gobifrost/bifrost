// Pure unit: the in-supervisor TenantGateway serves path-keyed module source
// like dev/executor's gateway (/api/sdk/modules/<path>,
// /api/sdk/modules-resolve), pin-verified against the manifest, holding the
// run token parent-side while callers present only DUMMY_CREDENTIAL.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createTenantGateway, isStoragePath, basePathForName} from './tenant-gateway.mjs';
import {DUMMY_CREDENTIAL} from '../executor/gateway-injection.mjs';

const WF = 'workflows/wf.py';
const HELPER = 'workflows/helper.py';
const wfSrc = 'import workflows.helper\ndef main():\n    return {"ok": True}\n';
const helperSrc = 'X = 1\n';

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function manifestFor(entries) {
  const manifest = {};
  for (const [path, content] of Object.entries(entries)) manifest[path] = await sha256Hex(content);
  return manifest;
}

const get = (gateway, path) => gateway.fetch(new Request(`https://test-api.invalid${path}`, {
  headers: {Authorization: `Bearer ${DUMMY_CREDENTIAL}`},
}));

test('modules/<path> serves pin-verified source with an injection marker', async () => {
  const store = {[WF]: wfSrc};
  const gateway = createTenantGateway({store, manifest: await manifestFor(store), runToken: 'synthetic-run-token-12345'});
  const res = await get(gateway, `/api/sdk/modules/${WF}`);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('X-Gateway-Injected'), 'true');
  const body = await res.json();
  assert.equal(body.path, WF);
  assert.equal(body.content, wfSrc);
  assert.match(body.hash, /^[0-9a-f]{64}$/);
});

test('run token is held parent-side and never echoed', async () => {
  const store = {[WF]: wfSrc};
  const gateway = createTenantGateway({store, manifest: await manifestFor(store), runToken: 'synthetic-run-token-12345'});
  const res = await get(gateway, `/api/sdk/modules/${WF}`);
  const raw = JSON.stringify(await res.json());
  assert.ok(!raw.includes('synthetic-run-token-12345'), 'token must never ride the gateway response');
  assert.equal(gateway.runToken, 'synthetic-run-token-12345');
});

test('non-dummy bearer is rejected before any module byte', async () => {
  const store = {[WF]: wfSrc};
  const gateway = createTenantGateway({store, manifest: await manifestFor(store)});
  const res = await gateway.fetch(new Request(`https://test-api.invalid/api/sdk/modules/${WF}`, {
    headers: {Authorization: 'Bearer real-token-shaped.abc.def'},
  }));
  assert.equal(res.status, 401);
});

test('unpinned and digest-mismatched paths fail closed', async () => {
  const gateway = createTenantGateway({store: {[WF]: wfSrc}, manifest: {[HELPER]: await sha256Hex(helperSrc)}});
  assert.equal((await get(gateway, `/api/sdk/modules/${WF}`)).status, 404);
  const tampered = createTenantGateway({store: {[WF]: 'tampered'}, manifest: {[WF]: await sha256Hex(wfSrc)}});
  assert.equal((await get(tampered, `/api/sdk/modules/${WF}`)).status, 409);
  const badShape = createTenantGateway({store: {['x.py']: 'X=1'}, manifest: {['x.py']: 'not-hex'}});
  assert.equal((await get(badShape, '/api/sdk/modules/x.py')).status, 404);
  assert.equal((await get(badShape, '/api/sdk/modules/.git/x.py')).status, 400);
});

test('modules-resolve maps import names to storage paths', async () => {
  const store = {[WF]: wfSrc, [HELPER]: helperSrc};
  const gateway = createTenantGateway({store, manifest: await manifestFor(store)});
  const mod = await (await get(gateway, '/api/sdk/modules-resolve?name=workflows.helper')).json();
  assert.equal(mod.kind, 'module');
  assert.equal(mod.storage_path, HELPER);
  const entry = await (await get(gateway, '/api/sdk/modules-resolve?name=workflows.wf')).json();
  assert.equal(entry.kind, 'module');
  assert.equal(entry.storage_path, WF);
  const ns = await (await get(gateway, '/api/sdk/modules-resolve?name=workflows')).json();
  assert.equal(ns.kind, 'namespace');
  const miss = await (await get(gateway, '/api/sdk/modules-resolve?name=ghost.deep')).json();
  assert.equal(miss.kind, 'not_found');
});

test('isStoragePath rejects escapes and non-python paths', () => {
  assert.equal(isStoragePath('workflows/wf.py'), true);
  assert.equal(isStoragePath('wf'), false);
  assert.equal(isStoragePath('/abs/wf.py'), false);
  assert.equal(isStoragePath('workflows/../evil.py'), false);
  assert.equal(isStoragePath('workflows\\evil.py'), false);
  assert.equal(isStoragePath('workflows/wf.txt'), false);
  assert.equal(basePathForName('workflows.helper'), 'workflows/helper');
  assert.equal(basePathForName('not a name!'), null);
});
