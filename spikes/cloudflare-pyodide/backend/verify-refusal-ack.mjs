import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const repo = new URL('../../../', import.meta.url);
const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL('results/refusal-ack/' + name, root), 'utf8');
const hash = value => createHash('sha256').update(value).digest('hex');
const receipt = JSON.parse(read('receipt.json')), sdk = JSON.parse(read('sdk.json'));
for (const name of ['attempt_journal.py', 'private_http_pool.py', 'workflow_request_mux.py',
  'test_private_http_pool.py', 'test_workflow_request_mux.py']) {
  assert.equal(hash(readFileSync(new URL('backend/' + name, root))), sdk.packet_sha256[name], name);
}
const allowed = new Set(['api/src/jobs/consumers/workflow_execution.py',
  'api/tests/unit/dev/test_pyodide_attempt_journal.py', 'spikes/cloudflare-pyodide/backend/attempt_journal.py']);
for (const line of read('pg-source.sha256').trim().split(/\r?\n/)) {
  const [digest, path] = line.split(/\s+/);
  assert.ok(allowed.delete(path));
  assert.equal(hash(readFileSync(new URL(path, repo))), digest, path);
}
assert.equal(allowed.size, 0);
assert.deepEqual(receipt.junit, {cases: 2, failures: 0, errors: 0, skipped: 0});
assert.match(read('focused.txt'), /Ran 45 tests[\s\S]*\bOK\b/);
for (const name of ['focused.txt', 'pg-quality.txt']) assert.match(read(name), /0 errors, 0 warnings/);
for (const report of [receipt, sdk]) {
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.physical_termination_verified, false);
  assert.deepEqual(report.running_after_cleanup, []);
}
assert.equal(read('running-after.txt').trim(), '');
assert.equal(sdk.qualification_passed, true);
assert.deepEqual(sdk.shared_pool_result.native_busy_counts, [2, 2, 1, 0]);
assert.equal(sdk.shared_pool_result.run_refresh_generation_redaction_verified, true);
assert.equal(sdk.shared_pool_result.failed_replay_unchanged_verified, true);
console.log('Private no-send ACK: 45 native cases, real PostgreSQL consumption/reconnect and real SDK control verified; public integration and deployed isolation remain open.');
