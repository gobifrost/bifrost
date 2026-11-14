import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root = new URL('../results/upstream-reconcile/', import.meta.url);
const receipt = JSON.parse(await readFile(new URL('receipt.json', root), 'utf8'));
const audit = JSON.parse(await readFile(new URL('runtime-source-audit.json', root), 'utf8'));
const environment = JSON.parse(await readFile(new URL('environment.json', root), 'utf8'));
assert.equal(receipt.source, audit.source_commit);
assert.equal(receipt.upstream, '025f6c019928d8be78bfe47f1cb62702b4ad2226');
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.deepEqual(receipt.junit, {cases: 129, failures: 0, errors: 0, skipped: 0});
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.deepEqual(receipt.running_after_cleanup, []);
const xml = await readFile(new URL('tests.xml', root), 'utf8');
assert.equal((xml.match(/<testcase\b/g) ?? []).length, 129);
assert.equal(audit.compared, 79);
assert.equal(audit.identical, 73);
assert.equal(audit.namespace_adapters, 6);
assert.deepEqual(audit.unexpected, []);
const hash = value => createHash('sha256').update(value).digest('hex');
assert.equal(environment.source, receipt.source);
assert.equal(environment.image_id, 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b');
assert.equal(environment.supervisor_sha256,
  hash(await readFile(new URL('ct128-upstream-reconcile.py', import.meta.url))));
for (const record of audit.records) {
  const text = (await readFile(new URL('../../../' + record.path, import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
  assert.equal(hash(text), record.source_sha256, record.path);
  assert.ok(['identical', 'explicit_namespace_adapter'].includes(record.state));
}
console.log('Current upstream merge: 129 native contracts pass; 73 closure modules identical and six explicit namespace adapters. No new deployment or isolate-admission claim.');
