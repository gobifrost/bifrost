// Executor capability-gate contract: declared workspace imports must resolve
// against the staged bundle manifest BEFORE any loader/network/SDK effect.
// Missing declarations fail closed as 422 capability-rejected with zero
// api_calls and without echoing secrets/params. Run with:
// node --test capability-requirements.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import {capabilityRejection} from './capability-requirements.mjs';

const manifest = {
  'workflows/pyodide_qualification_abc123.py': 'a'.repeat(64),
  'pyodide_helper_abc123.py': 'b'.repeat(64),
};

const envelope = (rejection) => Response.json({phase: 'capability-rejected',
  missing_modules: rejection.missing_modules, api_calls: []}, {status: 422});

test('admit present set passes with no rejection', () => {
  assert.equal(capabilityRejection(undefined, manifest), null);
  assert.equal(capabilityRejection({modules: []}, manifest), null);
  assert.equal(capabilityRejection({modules: ['workflows/pyodide_qualification_abc123.py']}, manifest), null);
  assert.equal(capabilityRejection({modules: Object.keys(manifest)}, manifest), null);
  assert.equal(capabilityRejection({modules: Object.keys(manifest).reverse()}, manifest), null);
});

test('admit missing set returns 422 capability-rejected shape with zero api_calls', async () => {
  const secret = 'Bearer S3CR3T-engine-token-xyz';
  const params = {n: 7, secret_param: 'param-S3CR3T'};
  const missingName = 'numpy_that_is_not_bundled_xyz';
  const rejection = capabilityRejection({modules: [missingName]}, manifest);
  assert.ok(rejection !== null, 'expected rejection for missing module');
  assert.deepEqual(rejection.missing_modules, [missingName]);
  const response = envelope(rejection);
  assert.equal(response.status, 422);
  const body = await response.json();
  assert.equal(body.phase, 'capability-rejected');
  assert.deepEqual(body.missing_modules, [missingName]);
  assert.deepEqual(body.api_calls, []);
  const encoded = JSON.stringify(body);
  assert.ok(!encoded.includes('S3CR3T'), 'envelope must not carry secrets');
  assert.ok(!encoded.includes('secret_param'), 'envelope must not carry params');
  assert.ok(!encoded.includes(secret), 'envelope must not carry engine token');
});

test('no manifest keeps old behavior unchanged', () => {
  assert.equal(capabilityRejection(undefined, undefined), null);
  assert.equal(capabilityRejection({modules: ['numpy_that_is_not_bundled_xyz']}, undefined), null);
  assert.equal(capabilityRejection({modules: ['a.py']}, null), null);
  assert.equal(capabilityRejection({modules: ['a.py']}, 'not-a-record'), null);
});

test('invalid requirements shape fails closed without echo', () => {
  for (const bad of [{modules: 'x'}, {modules: [42]}, {modules: ['']},
      {modules: Array(33).fill('a.py')}, 'modules', null, []]) {
    const rejection = capabilityRejection(bad, manifest);
    assert.ok(rejection !== null, `expected rejection for ${JSON.stringify(bad)?.slice(0, 40)}`);
    assert.deepEqual(rejection.missing_modules, []);
  }
});
