import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url);
const read = name => readFileSync(new URL('results/import-provenance/' + name, root), 'utf8').replace(/^\uFEFF/, '');
const receipt = JSON.parse(read('receipt.json'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
for (const name of ['workspace_prepare.py', 'test_workspace_prepare.py']) {
  assert.equal(hash(execFileSync('git', ['show', 'c45ad55ec47c26cfe0ab4a7beb6b6f7511e98cdf:spikes/cloudflare-pyodide/backend/' + name])), receipt.packet_sha256[name], name);
}
const source = commit => execFileSync('git', ['show', commit + ':api/src/services/execution/virtual_import.py']);
const native = source(receipt.api_source);
assert.ok(read('source-readback.sha256').includes(hash(native)));
const excluded = bytes => {
  const literal = bytes.toString('utf8').split('STDLIB_PREFIXES = frozenset([')[1]?.split('])')[0];
  assert.ok(literal);
  return [...literal.matchAll(/^\s*"([^"]+)"/gm)].map(match => match[1]).sort();
};
assert.deepEqual(excluded(native), excluded(source(receipt.runtime_engine_source)));
assert.equal(excluded(native).length, 112);
assert.equal(receipt.qualification_passed, true);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
assert.equal(receipt.cloud_invocations, 0);
assert.deepEqual(receipt.running_after_cleanup, []);
const proof = receipt.shared_pool_result;
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
for (const key of ['api_preparation_verified', 'both_real_sdk_effects_held_concurrently',
  'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified',
  'canonical_settled_accounting_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(proof[key], true, key);
console.log('Upstream excluded-root provenance refusal and genuine local workspace control verified.');
