import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/retained-contract/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const report = JSON.parse(await read('receipt.json'));
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.deepEqual(report.junit, {cases: 38, failures: 0, errors: 0, skipped: 0});
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.prototype_restored, true);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
for (const name of ['attempt_journal.py', 'private_http_pool.py', 'workflow_request_mux.py',
  'test_private_http_pool.py', 'ct128-retained-contract.py']) {
  const source = execFileSync('git', ['show', '6d3ba782214129c8a4132ae6af5620797479cb5e:spikes/cloudflare-pyodide/backend/' + name]);
  assert.equal(createHash('sha256').update(source).digest('hex'), report.packet_sha256[name], name);
}
const test = execFileSync('git', ['show', 'cceccf3abe81af72820991a07fd00f7263fd455a:api/tests/unit/dev/test_pyodide_attempt_journal.py']);
assert.equal(createHash('sha256').update(test).digest('hex'), report.postgres_test_sha256);
assert.match(await read('unit.txt'), /Ran 48 tests[\s\S]*\bOK\b/);
assert.match(await read('quality.txt'), /0 errors, 0 warnings/);
console.log('Retained companion terminal contract: 48 native unit cases, 38 PostgreSQL cases and restored/clean guest verified. Actual HTTP/engine retention and cloud acceptance remain unqualified.');
