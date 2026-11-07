import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, mkdtempSync, rmSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const read = path => readFileSync(new URL(path, import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(read('../results/https-binding/v4.json'));
for (const name of ['https_policy_binding.py', 'test_https_policy_binding.py',
  'private_http_pool.py', 'test_private_http_pool.py', 'shared-native-pool.py', 'ct128-canonical-failure.py']) {
  const bytes = ['shared-native-pool.py', 'ct128-canonical-failure.py'].includes(name)
    ? execFileSync('git', ['show', '75b872cf19e56a808c3fa93108172bbc35614c9a:spikes/cloudflare-pyodide/backend/' + name]) : read(name);
  assert.equal(hash(bytes), receipt.packet_sha256[name], name);
}
assert.equal(hash(read('fixture-https-policy.mjs')), receipt.packet_sha256['project/backend/fixture-https-policy.mjs']);
const entry = read('../results/https-binding/entry.mjs');
assert.equal(hash(entry), receipt.packet_sha256['project/backend/authorized-local-entry.mjs']);
const temporary = mkdtempSync(join(tmpdir(), 'https-binding-proof-'));
try {
  const output = join(temporary, 'entry.mjs');
  const generator = join(temporary, 'prepare.mjs');
  writeFileSync(generator, execFileSync('git', ['show', '75b872cf19e56a808c3fa93108172bbc35614c9a:spikes/cloudflare-pyodide/backend/prepare-canonical-https.mjs']));
  writeFileSync(join(temporary, 'authorized-local-entry.mjs'), read('authorized-local-entry.mjs'));
  execFileSync(process.execPath, [generator, output]);
  assert.deepEqual(readFileSync(output), entry);
} finally {rmSync(temporary, {recursive: true, force: true});}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.ok(receipt.commands.every(command => command.exit_code === 0));
const proof = receipt.shared_pool_result;
assert.deepEqual(Object.values(proof.launches), [1, 1]);
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
assert.deepEqual(Object.values(proof.attempts).map(attempt => attempt.canonical_status).sort(), ['Failed', 'Success']);
for (const field of ['stale_terminal_owner_rejected', 'duplicate_dispatch_reused_peer',
  'lost_ack_reconnect_verified', 'failed_replay_unchanged_verified', 'fixture_worker_cascade_cleanup_verified',
  'mux_retired_after_canonical_ack']) assert.equal(proof[field], true, field);
for (const attempt of Object.values(proof.attempts)) {
  assert.ok(attempt.preparation.requirements.includes('platform-fetch-https'));
  assert.equal(attempt.access_after_terminal_status, 401);
  assert.equal(attempt.evidence.runtime_cleanup.sdk_close_returned, true);
  assert.ok(attempt.persisted_logs.some(message => message.includes('synthetic_https_qualified')));
}
assert.deepEqual(receipt.synthetic_https_effects.map(effect => `${effect.body}:${effect.path}`).sort(),
  ['fails-first:/redirect', 'fails-first:/write', 'succeeds-second:/redirect', 'succeeds-second:/write']);
assert.ok(receipt.synthetic_https_effects.every(effect => effect.vendor_authorization_matches));
const failed = JSON.parse(read('../results/https-binding/v3.json'));
assert.equal(failed.qualification_passed, false);
assert.deepEqual(failed.running_after_cleanup, []);
assert.equal(failed.shared_pool_result, undefined);
console.log('Named tenant HTTPS binding and canonical replay verified locally; preclaim/public/cloud acceptance absent.');
