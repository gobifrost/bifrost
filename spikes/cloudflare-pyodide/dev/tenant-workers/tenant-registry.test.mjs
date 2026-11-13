// Pure unit: tenant registry determinism and rotation policy.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {tenantWorkerId} from './tenant-registry.mjs';

test('same tenant id always maps to the same worker id', async () => {
  const a = await tenantWorkerId('org-123');
  const b = await tenantWorkerId('org-123');
  assert.equal(a, b);
});

test('different tenants map to different worker ids', async () => {
  const a = await tenantWorkerId('org-aaa');
  const b = await tenantWorkerId('org-bbb');
  assert.notEqual(a, b);
});

test('worker id is a safe dynamic-dispatch name', async () => {
  const id = await tenantWorkerId('org-123');
  assert.match(id, /^[a-z0-9-]{1,128}$/);
});

test('invalid tenant ids throw (fail closed, no worker created)', async () => {
  await assert.rejects(() => tenantWorkerId(''), TypeError);
  await assert.rejects(() => tenantWorkerId(123), TypeError);
  await assert.rejects(() => tenantWorkerId('x'.repeat(257)), TypeError);
});
