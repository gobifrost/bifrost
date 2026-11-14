// Offline artifact/source verification, not physical-stop acceptance.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {CPU_CASES, classifyCpuPlacement} from './cpu-placement.mjs';

const read = relative => readFile(new URL(relative, import.meta.url));
const digest = value => createHash('sha256').update(value).digest('hex');
const old = JSON.parse(await read('../results/cpu-placement/local-v1.json'));
const snapshot = JSON.parse(await read('../results/cpu-placement/local-v1-source.json'));
for (const [name, source] of Object.entries(snapshot)) assert.equal(digest(source), old.packet_sha256[name]);
// Preserve the defect: v1's CPU outcome completed, but the original source's
// normal CPU return omitted after. It must not be treated as cessation.
assert.equal(old.observation.outcomes[1].body.body.total, 19999999900000000);
assert.deepEqual(old.observation.observations.code_limit.map(value => value.event), ['before']);

const receipt = JSON.parse(await read('../results/cpu-placement/local-v2.json'));
for (const [name, hash] of Object.entries(receipt.packet_sha256)) {
  const source = ['package.json', 'cloudflare.config.ts', 'wrangler.config.ts'].includes(name)
    ? `./cpu-placement-fixture/${name}` : `./${name}`;
  assert.equal(digest(await read(source)), hash, `Exact tested source: ${source}`);
}
assert.equal(receipt.bundle_sha256, '3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260');
assert.equal(receipt.driver_exit_code, 0);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.equal(receipt.state_before_cleanup.OOMKilled, false);
assert.deepEqual(receipt.running_after_cleanup, []);
for (const bound of ['--network=none', '--cpus=1', '--memory=1g', '--memory-swap=1g', '--pids-limit=256']) {
  assert.ok(receipt.command.includes(bound));
}
for (const [index, name] of CPU_CASES.entries()) {
  const outcome = receipt.observation.outcomes[index];
  assert.equal(outcome.case_id, name);
  assert.equal(outcome.http_status, 200);
  assert.equal(outcome.body.status, 200);
  assert.deepEqual(outcome.body.body.abi, {python: [3, 14, 2], pyodide: '314.0.6', soabi: 'cpython-314-wasm32-emscripten'});
  const events = receipt.observation.observations[name];
  assert.deepEqual(events.map(value => value.event), ['before', 'after']);
  assert.ok(events[1].elapsed_ms > events[0].elapsed_ms);
  assert.deepEqual(classifyCpuPlacement(name, events.map(value => value.event)), receipt.observation.classifications[index]);
  if (name !== 'completion') assert.equal(outcome.body.body.total, 19999999900000000);
}
console.log(JSON.stringify({exact_tested_packet_files: Object.keys(receipt.packet_sha256).length,
  local_api_and_abi_qualified: true, cloud_invocations: 0, physical_termination_verified: false}));
