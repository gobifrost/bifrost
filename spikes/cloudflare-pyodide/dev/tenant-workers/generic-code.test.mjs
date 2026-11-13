// Pure unit: the generic worker script is CONSTANT per tenant.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {GENERIC_PYTHON, genericWorkerCode, genericCodeDigest} from './generic-worker-code.mjs';

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
  assert.ok(GENERIC_PYTHON.includes('class Default(WorkerEntrypoint)'));
});

test('child env is empty: the child holds NO credentials', () => {
  assert.deepEqual(genericWorkerCode().env, {});
});
