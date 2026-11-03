import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {parseLosslessJson} from './lossless-json.mjs';
const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL('results/json-transport/' + name, root), 'utf8').replace(/^\uFEFF/, '');
const hash = value => createHash('sha256').update(value).digest('hex');
const before = JSON.parse(read('raw.json')), after = JSON.parse(read('after.json'));
const old = execFileSync('git', ['show', 'a63033f6575cb22c901e141351c3f6eb915881d8:spikes/cloudflare-pyodide/backend/lossless-json.mjs']);
assert.equal(before.adapter_sha256, hash(old));
assert.equal(after.adapter_sha256, hash(readFileSync(new URL('backend/lossless-json.mjs', root))));
for (const result of [before, after]) {
  assert.equal(result.source_sha256, hash(readFileSync(new URL('backend/json-transport-benchmark.mjs', root))));
  assert.equal(result.samples, 50);
  assert.equal(result.cloud_invocations, 0);
  assert.equal(result.precision_control.baseline_exact, false);
  assert.equal(result.precision_control.lossless_exact, true);
  for (const fixture of result.cases) for (const values of Object.values(fixture.wall_ms)) {
    assert.equal(values.length, 50);
    assert.ok(values.every(value => Number.isFinite(value) && value >= 0));
  }
}
assert.deepEqual(before.cases.map(value => value.wire_sha256), after.cases.map(value => value.wire_sha256));
const receipt = parseLosslessJson(read('runtime-receipt.json'));
assert.equal(receipt.packet_sha256['project/backend/lossless-json.mjs'], after.adapter_sha256);
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.shared_pool_result.exact_integer_roundtrip_verified, true);
assert.equal(receipt.shared_pool_result.failed_replay_unchanged_verified, true);
const peer = Object.values(receipt.shared_pool_result.attempts).find(value => value.label === 'succeeds-second');
assert.equal(JSON.stringify(peer.canonical_result.exact_integer), '9007199254740993');
console.log('Raw parent JSON benchmark and real numeric engine/SDK control verified; cloud performance unmeasured.');
