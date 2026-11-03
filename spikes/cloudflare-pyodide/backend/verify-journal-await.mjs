import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';

const base = new URL('../results/journal-await/', import.meta.url);
const receipt = JSON.parse(await readFile(new URL('receipt.json', base), 'utf8'));
assert.deepEqual(receipt.junit, {cases: 38, failures: 0, errors: 0, skipped: 0});
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.cloud_invocations, 0);
const paths = {'test_pyodide_attempt_journal.py': '../../../api/tests/unit/dev/test_pyodide_attempt_journal.py',
  'test_attempt_journal_owners.py': './test_attempt_journal_owners.py', 'attempt_journal.py': './attempt_journal.py'};
for (const [name, path] of Object.entries(paths)) {
  const relative = new URL(path, import.meta.url).pathname.split('/spikes/')[1];
  const repoPath = name === 'test_pyodide_attempt_journal.py'
    ? 'api/tests/unit/dev/' + name : 'spikes/' + relative;
  const tested = execFileSync('git', ['show', 'b6e4dfd4c28e6859c4c6319a1e8a664c221eba84:' + repoPath]);
  assert.equal(createHash('sha256').update(tested).digest('hex'), receipt.overlay_sha256[name]);
}
const unit = await readFile(new URL('unit.txt', base), 'utf8');
assert.match(unit, /Ran 6 tests/);
assert.match(unit, /\bOK\b/);
console.log(JSON.stringify({postgres_journal_cases: 38, owner_unit_cases: 6,
  alert_state_readback: 'denied_http_403', cloud_invocations: 0, physical_termination_verified: false}));
