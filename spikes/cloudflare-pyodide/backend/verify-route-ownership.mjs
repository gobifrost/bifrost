import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root = new URL('../results/route-ownership/', import.meta.url);
const receipt = JSON.parse(await readFile(new URL('receipt.json', root), 'utf8'));
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.deepEqual(receipt.junit, {cases: 38, failures: 0, errors: 0, skipped: 0});
assert.deepEqual(receipt.running_after_cleanup, []);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
const baseline = await readFile(new URL('baseline.log', root), 'utf8');
assert.match(baseline, /Ran 6 tests/);
assert.match(baseline, /FAILED \(failures=5\)/);
const after = await readFile(new URL('after-0.log', root), 'utf8');
assert.match(after, /Ran 6 tests/);
assert.match(after, /\nOK\s*$/);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
for (const [path, expected] of Object.entries(receipt.overlay_sha256)) {
  assert.equal(hash(await readFile(new URL('../../../' + path, import.meta.url))), expected, path);
}
assert.equal(hash(await readFile(new URL('test_attempt_journal_owners.py', import.meta.url))), receipt.unit_fixture_sha256);
console.log('Journal route ownership: five baseline failures repaired; six focused and 38 PostgreSQL-backed regressions pass. No cloud/physical-stop claim.');
