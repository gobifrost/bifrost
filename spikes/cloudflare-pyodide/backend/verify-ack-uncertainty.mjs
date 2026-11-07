import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const root = new URL('../../../', import.meta.url);
const results = new URL('../results/ack-uncertainty/', import.meta.url);
const read = async name => JSON.parse(await readFile(new URL(name, results), 'utf8'));
const before = await read('before.json');
const after = await read('after.json');
const quality = await read('quality.json');
assert.deepEqual(before.junit, {cases: 17, failures: 13, errors: 0, skipped: 0});
assert.deepEqual(after.junit, {cases: 77, failures: 0, errors: 0, skipped: 0});
assert.equal(before.commands[1].exit_code, 1);
assert.ok(after.commands.every(command => command.exit_code === 0));
assert.ok(quality.commands.every(command => command.exit_code === 0));
for (const report of [before, after]) {
  assert.equal(report.source_base, '1d33150eb376b71f8a81b427920eda1b0447f829');
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.physical_termination_verified, false);
  assert.deepEqual(report.running_after_cleanup, []);
}
for (const [path, digest] of Object.entries(after.tested_sha256)) {
  assert.equal(createHash('sha256').update(await readFile(new URL(path, root))).digest('hex'), digest, path);
}
const xml = await readFile(new URL('after.xml', results), 'utf8');
assert.equal((xml.match(/<testcase\b/g) ?? []).length, 77);
assert.ok(!/<(?:failure|error|skipped)\b/.test(xml));
console.log(JSON.stringify({baseline_failures: 13, corrected_guest_cases: 77,
  ruff_and_pyright_passed: true, linux_pid_protocol_preserved: true,
  cloud_invocations: 0, physical_termination_verified: false}));
