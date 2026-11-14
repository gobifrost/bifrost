import assert from 'node:assert/strict';
import test from 'node:test';
import {parseLosslessJson, boundedLosslessJson} from './lossless-json.mjs';

test('opaque numeric lexemes survive a parent roundtrip without changing safe control integers', () => {
  const wire = '{"timeout_seconds":30,"values":[9007199254740993,-9007199254740993,123456789012345678901234567890,1.0,-0.0,1e20,0.1],"text":"9007199254740993"}';
  const parsed = parseLosslessJson(wire);
  assert.equal(parsed.timeout_seconds, 30);
  assert.equal(parsed.text, '9007199254740993');
  assert.equal(JSON.stringify(parsed), wire);
});

test('native JSON grammar rejects executable text and raw object injection', () => {
  for (const wire of ['NaN', 'Infinity', '{"value":1};process.exit(0)', '{"value":01}']) {
    assert.throws(() => parseLosslessJson(wire), SyntaxError);
  }
});

test('lossless response parsing retains the existing byte bound', async () => {
  await assert.rejects(boundedLosslessJson(new Response('12345678901234567890'), 10), /byte limit/);
});

test('numeric screening preserves root, array and object tokens across boundaries', () => {
  for (const token of ['0', '1', '-1', '999999999999999', '-999999999999999',
    '1000000000000000', '9007199254740993', '-9007199254740993', '123456789012345678901234567890',
    '-0', '-0.0', '0.0', '1.0', '0.1', '1e20', '-1E-20', '1e300']) {
    for (const wire of [token, `[${token}]`, `{"value":${token}}`, `{"before":[0,${token}],"after":true}`]) {
      assert.equal(JSON.stringify(parseLosslessJson(wire)), wire, wire);
      assert.equal(JSON.stringify(parseLosslessJson(' \n' + wire + '\t')), wire, wire);
    }
  }
});

test('numeric-looking string text cannot select a lossy parse for real numbers', () => {
  const wire = '{"text":"escaped \\\" :9007199254740993,1e20","value":9007199254740993}';
  assert.equal(JSON.stringify(parseLosslessJson(wire)), wire);
});
