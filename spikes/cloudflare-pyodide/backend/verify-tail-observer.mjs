import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const base = new URL('../results/tail-observer/', import.meta.url);
const json = async name => JSON.parse(await readFile(new URL(name, base), 'utf8'));
const old = await json('v1.json');
assert.equal(createHash('sha256').update(await readFile(new URL('original-entry.txt', base))).digest('hex'), old.packet_sha256['tail-observer-entry.mjs']);
assert.deepEqual(old.observation.records.map(row => row.mode), ['js_completion', 'js_background']);
assert.deepEqual(old.observation.records[1].events.map(event => event.event), ['before']);
const receipt = await json('v2.json');
for (const [name, hash] of Object.entries(receipt.packet_sha256)) {
  const relative = ['package.json', 'cloudflare.config.ts', 'wrangler.config.ts'].includes(name) ? `./tail-observer-fixture/${name}` : `./${name}`;
  assert.equal(createHash('sha256').update(await readFile(new URL(relative, import.meta.url))).digest('hex'), hash, name);
}
for (const report of [old, receipt]) {
  assert.equal(report.driver_exit_code, 0);
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.physical_termination_verified, false);
  assert.equal(report.state_before_cleanup.OOMKilled, false);
  assert.deepEqual(report.running_after_cleanup, []);
}
const rows = receipt.observation.records;
assert.deepEqual(rows.map(row => row.mode), ['js_completion', 'js_background', 'js_io_abort', 'python_io', 'python_cpu']);
for (const row of rows) {
  const tails = row.events.filter(event => event.event === 'native_tail');
  assert.equal(tails.length, 1);
  assert.equal(tails[0].metadata.outcome, 'ok');
  assert.ok(!JSON.stringify(row).includes('synthetic-tail-secret'));
  assert.deepEqual(tails[0].metadata.raw_numeric_fields, {cpuTime: 0, cpuTimeMs: null, wallTime: 0, wallTimeMs: null});
}
const background = rows[1].events.find(event => event.event === 'background_after');
assert.ok(background.elapsed_ms > rows[1].response_elapsed_ms);
assert.ok(rows[1].events.find(event => event.event === 'native_tail').elapsed_ms > background.elapsed_ms);
assert.equal(rows[2].outcome.body.error_type, 'AbortError');
assert.deepEqual(rows[2].events.map(event => event.event), ['before', 'parent_abort_requested', 'native_tail']);
for (const row of rows.slice(3)) {
  assert.deepEqual(row.outcome.body.outcome.body.abi, {python: [3, 14, 2], pyodide: '314.0.6', soabi: 'cpython-314-wasm32-emscripten'});
}
assert.equal(rows[4].outcome.body.outcome.body.total, 19999999900000000);
console.log(JSON.stringify({local_tail_controls: 5, post_wait_until_order_verified: true,
  canceled_io_tail_outcome: 'ok', usable_local_cpu_metrics: false,
  cloud_invocations: 0, physical_termination_verified: false}));
