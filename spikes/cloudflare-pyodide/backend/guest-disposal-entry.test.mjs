import assert from 'node:assert/strict';
import test from 'node:test';
import worker, {feature} from './guest-disposal-entry.mjs';

test('feature inspection does not invoke a supported disposer', () => {
  const key = Symbol('dispose');
  let calls = 0;
  assert.deepEqual(feature({[key]() { calls++; }}, key), {available: true});
  assert.equal(calls, 0);
});
test('a missing Symbol cannot be mistaken for a dynamic method named undefined', () => {
  assert.deepEqual(feature({undefined() {}}, undefined), {available: false, symbol_available: false});
  assert.deepEqual(feature({}, Symbol('missing')), {available: false});
});
test('unsupported symbol lookup errors remain explicitly unavailable', () => {
  const key = Symbol('dispose');
  const target = Object.defineProperty({}, key, {get() { throw new TypeError('unsupported'); }});
  assert.deepEqual(feature(target, key), {available: false, error_type: 'TypeError'});
});
test('local-only and POST gates reject before creating any runtime', async () => {
  const env = {LOCAL_PROBE: 'true', LOADER: {load() { throw new Error('Must not create runtime'); }}};
  assert.equal((await worker.fetch(new Request('https://probe.invalid/'), env)).status, 403);
  assert.equal((await worker.fetch(new Request('https://probe.invalid/', {method: 'POST'}), {...env, LOCAL_PROBE: 'false'})).status, 403);
});
