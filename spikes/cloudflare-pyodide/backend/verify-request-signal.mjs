import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {classifySignalRecord} from './request-signal-evidence.mjs';
const base = new URL('../results/request-signal/', import.meta.url);
for (const mode of ['disabled', 'enabled']) {
  const receipt = JSON.parse(await readFile(new URL(`${mode}.json`, base), 'utf8'));
  assert.equal(receipt.mode, mode);
  assert.equal(receipt.driver_exit_code, 0);
  assert.equal(receipt.cloud_invocations, 0);
  assert.equal(receipt.state_before_cleanup.OOMKilled, false);
  assert.deepEqual(receipt.running_after_cleanup, []);
  for (const [name, hash] of Object.entries(receipt.packet_sha256)) {
    const url = name === 'request-signal.test.mjs' ? new URL('original-test.txt', base)
      : new URL(['package.json', 'wrangler.config.ts', 'cloudflare.config.ts'].includes(name)
        ? `./request-signal-fixture/${name}` : `./${name}`, import.meta.url);
    assert.equal(createHash('sha256').update(await readFile(url)).digest('hex'), hash, name);
  }
  const [control, ...aborts] = receipt.observation.records;
  assert.deepEqual(control.outcome.body, {python: [3, 14, 2], pyodide: '314.0.6', soabi: 'cpython-314-wasm32-emscripten'});
  assert.equal(classifySignalRecord(control).classification, 'completion_control_passed');
  for (const record of aborts) {
    assert.equal(record.outcome.error_type, 'AbortError');
    assert.equal(classifySignalRecord(record).classification, 'incoming_abort_not_observed');
    assert.deepEqual(record.events.map(event => event.event), ['before', 'abort_requested', 'after']);
  }
}
console.log(JSON.stringify({local_cases: 6, incoming_abort_delivery_qualified: false,
  physical_termination_verified: false, cloud_invocations: 0}));
