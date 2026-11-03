import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url);
const read = name => JSON.parse(readFileSync(new URL('results/sdk-close/' + name + '.json', root), 'utf8').replace(/^\uFEFF/, ''));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const probe = read('probe');
for (const value of Object.values(probe.observation.body.client_close)) assert.equal(value, true);
assert.equal(probe.running_after_cleanup, 'false');
assert.equal(hash(execFileSync('git', ['show', '469846d91b3527084be60a390ec5fafa4bd3014e:spikes/cloudflare-pyodide/backend/latest-sdk-probe-entry.mjs'])), probe.packet_sha256['project/backend/latest-sdk-probe-entry.mjs']);
for (const name of ['updated', 'baseline']) {
  const receipt = read(name);
  assert.equal(receipt.qualification_passed, true);
  assert.equal(receipt.cloud_invocations, 0);
  assert.equal(receipt.physical_termination_verified, false);
  assert.deepEqual(receipt.running_after_cleanup, []);
  assert.ok(receipt.commands.every(command => command.exit_code === 0));
  const code = execFileSync('git', ['show', '469846d91b3527084be60a390ec5fafa4bd3014e:spikes/cloudflare-pyodide/backend/runtime-code.mjs'], {encoding: 'utf8'});
  const selected = name === 'updated' ? code.replace("'./generated-runtime.mjs'", "'./generated-runtime-sdk.mjs'") : code;
  assert.equal(hash(selected), receipt.packet_sha256['project/backend/runtime-code.mjs']);
  for (const file of ['authorized-local-entry.mjs', 'ct128-canonical-failure.py']) {
    const source = file.endsWith('.py')
      ? execFileSync('git', ['show', '469846d91b3527084be60a390ec5fafa4bd3014e:spikes/cloudflare-pyodide/backend/' + file])
      : readFileSync(new URL('backend/' + file, root));
    assert.equal(hash(source),
      receipt.packet_sha256[file.endsWith('.py') ? file : 'project/backend/' + file]);
  }
  const proof = receipt.shared_pool_result;
  assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
  assert.equal(proof.failed_replay_unchanged_verified, true);
  assert.equal(proof.canonical_settled_accounting_verified, true);
  for (const attempt of Object.values(proof.attempts)) {
    assert.equal(attempt.evidence.runtime_cleanup.sdk_close_returned, true);
    assert.equal(attempt.access_after_terminal_status, 401);
  }
}
console.log('Real SDK close and both real workflow SDK versions verified locally; no isolate-stop claim.');
