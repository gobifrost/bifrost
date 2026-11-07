import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const engine = JSON.parse(read('cooperative-overflow-v9.json'));
const runtime = JSON.parse(read('rpc-allocation-v15.json'));
assert.equal(engine.qualification_passed, true);
assert.equal(engine.shared_pool_result.rpc_wire_budget_failure_verified, true);
assert.equal(engine.shared_pool_result.terminal_obligations_released, true);
assert.deepEqual(engine.running_after_cleanup, []);
assert.equal(engine.cloud_invocations, 0);
for (const name of ['cooperative-runtime.mjs', 'cooperative-rpc-result.mjs']) {
  const source = name === 'cooperative-runtime.mjs' ? execFileSync('git',
    ['show', '4c03d117d:spikes/cloudflare-pyodide/backend/cooperative-runtime.mjs']) : read(name);
  assert.equal(engine.packet_sha256[`project/backend/${name}`], sha(source));
}
const staged = `export default ${JSON.stringify(read('rpc_serialization.py'))};`;
assert.equal(engine.packet_sha256['project/backend/rpc-serialization-code.mjs'], sha(staged));
assert.equal(runtime.packet_sha256['project/backend/rpc-serialization-code.mjs'], sha(staged));
const failed = Object.values(engine.shared_pool_result.attempts).find(item => item.canonical_status === 'Failed');
assert.equal(failed.evidence.result.error_type, 'ExecutorRpcResultTooLargeError');
assert.equal(failed.evidence.provider_outcome.kind, 'rpc_result_too_large');
assert.equal(failed.evidence.provider_outcome.engine_outcome_delivered, false);
assert.equal(failed.evidence.runtime_cleanup.sdk_close_returned, true);
assert.deepEqual([runtime.observation.body.tests_run, runtime.observation.body.failures, runtime.observation.body.errors], [8, 0, 0]);
assert.equal(runtime.running_after_cleanup, 'false');
assert.equal(runtime.cloud_invocations, 0);
assert.equal(runtime.observation.body.allocation.available, true);
assert.deepEqual(runtime.observation.body.allocation.samples.map(item => item.delivery_failure), [false, false, true]);
assert.equal(runtime.observation.body.allocation.input_allocation_excluded, true);
console.log('Actual engine RPC overflow mapping, Pyodide tests and scoped allocation observations verified.');
