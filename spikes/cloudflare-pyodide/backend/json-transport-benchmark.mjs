import assert from 'node:assert/strict';
import {performance} from 'node:perf_hooks';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {parseLosslessJson} from './lossless-json.mjs';

const samples = 50;
const cases = [
  ['small_json', JSON.stringify({rows: Array.from({length: 100}, (_, id) => ({id, active: true, label: 'synthetic-row'}))})],
  ['medium_json', JSON.stringify({rows: Array.from({length: 2000}, (_, id) => ({id, active: true, label: 'synthetic-row'}))})],
  ['near_retention', JSON.stringify({payload: 'x'.repeat(900000)})],
];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const report = {scope: 'Node parent JSON roundtrip only; not Pyodide/cloud/SDK latency',
  node: process.version, v8: process.versions.v8, samples, cloud_invocations: 0,
  source_sha256: hash(readFileSync(new URL(import.meta.url))),
  adapter_sha256: hash(readFileSync(new URL('./lossless-json.mjs', import.meta.url))), cases: []};
for (const [name, wire] of cases) {
  const timings = {baseline: [], lossless: []};
  const parsers = {baseline: JSON.parse, lossless: parseLosslessJson};
  for (const parse of Object.values(parsers)) assert.equal(JSON.stringify(parse(wire)), wire);
  // Alternating order exposes both implementations to the same quota/GC regime.
  for (let index = 0; index < samples; index++) {
    for (const mode of index % 2 ? ['lossless', 'baseline'] : ['baseline', 'lossless']) {
      const start = performance.now();
      const encoded = JSON.stringify(parsers[mode](wire));
      timings[mode].push(performance.now() - start);
      assert.equal(encoded.length, wire.length);
    }
  }
  report.cases.push({name, bytes: Buffer.byteLength(wire), wire_sha256: hash(wire),
    wall_ms: timings, correctness: 'exact roundtrip on generated safe-number fixture'});
}
const precisionWire = '{"value":9007199254740993}';
report.precision_control = {
  baseline_exact: JSON.stringify(JSON.parse(precisionWire)) === precisionWire,
  lossless_exact: JSON.stringify(parseLosslessJson(precisionWire)) === precisionWire,
};
assert.equal(report.precision_control.baseline_exact, false);
assert.equal(report.precision_control.lossless_exact, true);
console.log(JSON.stringify(report, null, 2));
