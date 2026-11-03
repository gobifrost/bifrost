import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {withCooperativeRpc} from './cooperative-runtime.mjs';

// Read the real existing entry template without importing the large generated bundle.
const runtime = readFileSync(new URL('runtime-code.mjs', import.meta.url), 'utf8');
const main = /const main=`([\s\S]*?)`;/u.exec(runtime)?.[1];
assert.equal(typeof main, 'string');
const cancellation = readFileSync(new URL('cooperative_execution.py', import.meta.url), 'utf8');
const serialization = readFileSync(new URL('rpc_serialization.py', import.meta.url), 'utf8');

test('candidate retains actual engine invocation, envelope mapping and owned SDK cleanup', () => {
  const code = {modules: {'main.py': {py: main}, 'existing.py': {py: 'unchanged'}}, env: {}, limits: {cpuMs: 1000}};
  const snapshot = structuredClone(code);
  const candidate = withCooperativeRpc(code, cancellation, serialization);
  const source = candidate.modules['main.py'].py;
  assert.match(source, /result = await run_authorized_attempt\(body\["execution_id"\],body\["context"\],body\["api_url"\], public_url=body\.get\("public_url"\)\)/);
  assert.match(source, /result = execution_result_envelope\(body\["execution_id"\], body\["worker_id"\], result,/);
  assert.match(source, /finally:\n            from bifrost.client import get_client\n            await close_sdk\(get_client\(\)\)\n            cleanup_evidence/);
  assert.match(source, /owner_scheduled=True/);
  assert.match(source, /return encode_engine_rpc/);
  assert.doesNotMatch(source, /json\.loads\(await response\.text\(\)\)/);
  assert.equal(candidate.modules['rpc_serialization.py'].py, serialization);
  assert.match(source, /Candidate RPC only/);
  assert.doesNotMatch(source, /"status": "Cancelled"/);
  assert.equal(candidate.modules['cooperative_execution.py'].py, cancellation);
  assert.equal(candidate.modules['existing.py'], code.modules['existing.py']);
  assert.deepEqual(code, snapshot);
  assert.equal(candidate.limits, code.limits);
});

test('unexpected or already-composed entry fails closed', () => {
  assert.throws(() => withCooperativeRpc({modules: {}}, cancellation));
  const code = {modules: {'main.py': {py: main}}};
  assert.throws(() => withCooperativeRpc(code, cancellation));
  assert.throws(() => withCooperativeRpc(withCooperativeRpc(code, cancellation, serialization), cancellation, serialization));
  assert.throws(() => withCooperativeRpc({modules: {'main.py': {py: main + main}}}, cancellation, serialization));
});
