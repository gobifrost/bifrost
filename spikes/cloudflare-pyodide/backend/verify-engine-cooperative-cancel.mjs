import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const receipt = JSON.parse(read('engine-cooperative-cancel-v4.json'));
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
const result = receipt.shared_pool_result;
assert.deepEqual(result.native_busy_counts, [2, 1, 1, 0]);
assert.equal(result.engine_cooperative_control.cancellation_requested, true);
assert.equal(result.engine_cooperative_control.physical_stopped, false);
assert.equal(result.engine_cooperative_control.execution_settled, false);
assert.equal(result.engine_result_before_vendor_release, true);
assert.equal(result.consumer_ack_deliberately_held, true);
assert.equal(result.physical_cancellation_guard_preserved, true);
assert.equal(result.full_native_cancel_delivery_verified, false);
assert.equal(result.vendor_before_cancel_release.committed, false);
assert.equal(receipt.vendor_after_release.committed, true);
assert.deepEqual(Object.values(result.launches), [1, 1]);
for (const attempt of Object.values(result.attempts)) {
  assert.equal(attempt.canonical_status, 'Success');
  assert.equal(attempt.access_after_terminal_status, 401);
  assert.equal(attempt.evidence.runtime_cleanup.sdk_close_returned, true);
}
const directory = mkdtempSync(join(tmpdir(), 'verify-engine-cancel-'));
try {
  const output = join(directory, 'fixture.py');
  execFileSync(process.execPath, [fileURLToPath(new URL('prepare-engine-cancel-fixture.mjs', import.meta.url)),
    fileURLToPath(new URL('shared-native-pool.py', import.meta.url)), output]);
  assert.equal(sha(readFileSync(output)), receipt.packet_sha256['shared-native-pool.py']);
} finally {rmSync(directory, {recursive: true, force: true});}
assert.equal(sha(read('cooperative-runtime.mjs')), receipt.packet_sha256['project/backend/cooperative-runtime.mjs']);
console.log('Actual engine cooperative cancellation/suppression evidence verified; physical stop remains unqualified.');
