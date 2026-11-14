import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../results/setup-refusal/', import.meta.url);
const read = name => readFileSync(new URL(name, root), 'utf8');
const probe = readFileSync(new URL('setup-refusal-probe.py', import.meta.url));
assert.equal(createHash('sha256').update(probe).digest('hex'), read('probe.sha256').split(/\s+/)[0]);
const source = read('source.txt').trim();
assert.equal(source, '1d33150eb376b71f8a81b427920eda1b0447f829');
for (const line of read('source.sha256').trim().split(/\r?\n/)) {
  const [digest, path] = line.split(/\s+/);
  const bytes = execFileSync('git', ['show', source + ':' + path]);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), digest, path);
}
const output = read('observation.txt');
const receipt = JSON.parse(output.split(/\r?\n/).find(line => line.startsWith('{"scope":')));
assert.equal(receipt.database_tested, false);
assert.equal(receipt.cloud_invocations, 0);
for (const observation of receipt.observations) {
  assert.equal(observation.execution_id_matches, true);
  assert.equal(observation.update_owner_argument_present, false);
  const raises = observation.case === 'write_raises';
  assert.equal(observation.returned_none, !raises);
  assert.equal(observation.error_type, raises ? 'ConnectionError' : null);
  for (const key of ['failure_publications', 'history_publications', 'sync_results']) assert.equal(observation[key], raises ? 0 : 1);
}
assert.equal(receipt.observations.length, 3);
assert.match(output, /0 errors, 0 warnings/);
console.log('Source-pinned negative setup-acknowledgement control reproduced; canonical refusal retirement remains unimplemented.');
