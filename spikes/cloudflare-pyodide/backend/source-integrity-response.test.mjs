import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {verifyModuleResponse} from './module-response.mjs';
import {sourceIntegrityResponse} from './source-integrity-response.mjs';

test('changed source is denied as HTTP 409 with structured provenance evidence', async () => {
  const changed = 'def fold(n):\n    return 999\n';
  const hash = createHash('sha256').update(changed).digest('hex');
  const trace = {method: 'GET', path: '/api/sdk/modules/helper.py', status: 200};
  const response = await sourceIntegrityResponse(() => verifyModuleResponse(
    Response.json({path: 'helper.py', content: changed, hash}), 'helper.py', 'a'.repeat(64)), trace);
  assert.equal(response.status, 409);
  assert.deepEqual(await response.json(), {detail: 'Workspace module digest mismatch'});
  assert.equal(trace.status, 409);
  assert.equal(trace.upstream_status, 200);
  assert.equal(trace.integrity_error, 'Workspace module digest mismatch');
});

test('successful responses and upstream authentication challenges are unchanged', async () => {
  for (const status of [200, 401, 403]) {
    const original = new Response('original', {status, headers: {'WWW-Authenticate': 'Bearer refresh_required'}});
    const trace = {status};
    const response = await sourceIntegrityResponse(async () => original, trace);
    assert.equal(response, original);
    assert.equal(response.headers.get('WWW-Authenticate'), 'Bearer refresh_required');
    assert.equal(trace.integrity_error, undefined);
  }
});

test('unrelated errors and provider limit failures are never relabeled or hidden', async () => {
  for (const error of [new Error('provider CPU limit'), new Error('Workspace module identity mismatch')]) {
    const trace = {status: 200};
    await assert.rejects(sourceIntegrityResponse(async () => {throw error;}, trace), value => value === error);
    assert.deepEqual(trace, {status: 200});
  }
});
