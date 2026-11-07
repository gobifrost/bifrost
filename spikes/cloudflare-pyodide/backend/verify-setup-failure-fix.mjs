import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const repo = new URL('../../../', import.meta.url);
const root = new URL('../results/setup-failure-fix/', import.meta.url);
const read = name => readFileSync(new URL(name, root), 'utf8');
const hash = value => createHash('sha256').update(value).digest('hex');
const receipt = JSON.parse(read('receipt.json'));
const allowed = new Set(['api/src/jobs/consumers/workflow_execution.py',
  'api/tests/unit/jobs/consumers/test_workflow_execution_lineage.py', 'api/tests/unit/dev/test_pyodide_attempt_journal.py']);
for (const line of read('final-source.sha256').trim().split(/\r?\n/)) {
  const [digest, path] = line.split(/\s+/);
  assert.ok(allowed.delete(path), path);
  const bytes = path === 'api/tests/unit/dev/test_pyodide_attempt_journal.py'
    ? execFileSync('git', ['show', 'b992ba3640ae74113dc5e6c59829812a11c246c4:' + path])
    : readFileSync(new URL(path, repo));
  assert.equal(hash(bytes), digest, path);
}
assert.equal(allowed.size, 0);
for (const line of read('before-source.sha256').trim().split(/\r?\n/)) {
  const [digest, path] = line.split(/\s+/);
  const bytes = path.endsWith('test_workflow_execution_lineage.py') ? read('before-lineage.py.txt')
    : execFileSync('git', ['show', receipt.base_source + ':' + path]);
  assert.equal(hash(bytes), digest);
}
assert.deepEqual(receipt.junit, {
  before: {cases: 4, failures: 3, errors: 0, skipped: 0},
  after: {cases: 11, failures: 0, errors: 0, skipped: 0},
  pg: {cases: 3, failures: 0, errors: 0, skipped: 0},
});
for (const name of ['quality.txt', 'pg-quality.txt']) assert.match(read(name), /0 errors, 0 warnings/);
assert.match(read('pg-quality-before.txt'), /read-only file system/);
assert.equal(read('pg-running-after.txt').trim(), '');
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.production_deployments, 0);
assert.equal(receipt.physical_termination_verified, false);
console.log('Claim-fenced generic setup failure: fail-first control, 11 consumer and 3 real PostgreSQL cases verified; no refusal-retirement or deployed-acceptance claim.');
