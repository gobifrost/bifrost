// Pure unit: the generic worker script is CONSTANT per tenant.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {GENERIC_PYTHON, ENGINE_PYTHON, genericWorkerCode, genericCodeDigest} from './generic-worker-code.mjs';

test('generic code is identical across calls (no per-tenant bake)', async () => {
  const a = genericWorkerCode();
  const b = genericWorkerCode();
  assert.deepEqual(a, b);
  assert.equal(await genericCodeDigest(), await genericCodeDigest());
});

test('digest is a stable sha256 over the script', async () => {
  assert.match(await genericCodeDigest(), /^[0-9a-f]{64}$/);
});

test('script contains no tenant identity and no per-attempt data', () => {
  assert.ok(!GENERIC_PYTHON.includes('org-'), 'must not bake tenant ids');
  assert.ok(!GENERIC_PYTHON.includes('tenant-py-'), 'must not bake worker ids');
  assert.ok(!GENERIC_PYTHON.includes('Bearer'), 'must not carry credentials');
  assert.ok(!GENERIC_PYTHON.includes('<tenant-data>/'), 'name-keyed data map is gone');
  assert.ok(GENERIC_PYTHON.includes('class Default(WorkerEntrypoint)'));
});

test('importer is path-keyed: records storage paths, maps names like production', () => {
  assert.ok(GENERIC_PYTHON.includes('class _PathImporter'), 'path-keyed importer present');
  assert.ok(!GENERIC_PYTHON.includes('_DataImporter'), 'old name-keyed importer removed');
  assert.ok(GENERIC_PYTHON.includes('<tenant-path>/'), 'tracebacks name storage paths');
  assert.ok(GENERIC_PYTHON.includes("__init__.py"), 'package entries resolve by path');
  assert.ok(GENERIC_PYTHON.includes('entry_path'), 'entry loads by storage path, not import name');
});

test('child env is empty: the child holds NO credentials', () => {
  assert.deepEqual(genericWorkerCode().env, {});
});

test('engine child re-establishes attempt-scoped SDK state at every attempt', () => {
  // One persistent child serves every attempt for a tenant. The SDK caches its
  // client per thread, so without this handoff a second attempt keeps presenting
  // the previous attempt's run handle and its SDK/module calls are refused.
  assert.ok(ENGINE_PYTHON.includes('from bifrost.client import _install_cached_access_token'));
  const handoff = ENGINE_PYTHON.indexOf('_install_cached_access_token(body["api_url"], token)');
  const run = ENGINE_PYTHON.indexOf('await run_authorized_attempt');
  assert.ok(handoff > 0, 'credential handoff present');
  assert.ok(handoff < run, 'handoff happens before the engine run starts');
});
