import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/inventory-uncertainty/', import.meta.url);
const bytes = name => readFile(new URL(name, base));
const read = async name => JSON.parse(await bytes(name));
const hash = value => createHash('sha256').update(value).digest('hex');
const reports = await Promise.all([1, 2, 3].map(n => read(`v${n}/receipt.json`)));
assert.equal(reports[0].before_pool_sha256, hash(execFileSync('git', ['show',
  '89464d31d:api/src/worker/remote/remote_pool.py'])));
for (const [index, report] of reports.entries()) {
  const revision = index + 1;
  assert.equal(report.qualification_passed, true);
  assert.deepEqual(report.before, {cases: revision + 2, failures: revision === 1 ? 2 : 1, errors: 0, skipped: 0});
  assert.deepEqual(report.after, {cases: 41 + revision, failures: 0, errors: 0, skipped: 0});
  assert.equal(report.commands[1].exit_code, 1);
  assert.ok(report.commands.filter((_, n) => n !== 1).every(command => command.exit_code === 0));
  assert.deepEqual(report.running_after_cleanup, []);
  assert.equal(report.after_pool_sha256, report.packet_sha256['remote_pool.py']);
  if (index) assert.equal(report.before_pool_sha256, reports[index - 1].after_pool_sha256);
  for (const phase of ['before', 'after']) {
    const xml = (await bytes(`v${revision}/${phase}.xml`)).toString();
    for (const [name, tag] of Object.entries({cases: 'testcase', failures: 'failure', errors: 'error', skipped: 'skipped'})) {
      assert.equal([...xml.matchAll(new RegExp('<' + tag + '\\b', 'g'))].length, report[phase][name]);
    }
  }
  if (revision < 3) {
    for (const [source, snapshot] of Object.entries({'remote_pool.py': 'remote_pool.txt',
      'test_pyodide_inventory_uncertainty.py': 'test.txt', 'ct128-inventory-uncertainty.py': 'runner.txt'})) {
      assert.equal(hash(await bytes(`v${revision}/${snapshot}`)), report.packet_sha256[source], source);
    }
  }
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.execution_admission_qualified, false);
  assert.equal(report.physical_termination_verified, false);
}
for (const [name, path] of Object.entries({
  'remote_pool.py': '../../../api/src/worker/remote/remote_pool.py',
  'test_pyodide_inventory_uncertainty.py': '../../../api/tests/unit/dev/test_pyodide_inventory_uncertainty.py',
  'ct128-inventory-uncertainty.py': './ct128-inventory-uncertainty.py',
})) assert.equal(hash(await readFile(new URL(path, import.meta.url))), reports[2].packet_sha256[name], name);
console.log('Fail-first opaque omission, live rejection, terminal replay and owner-fencing regressions; final 44 native cases pass with unchanged Linux controls. No cloud/physical-stop acceptance.');
