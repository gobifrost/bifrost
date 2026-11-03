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
