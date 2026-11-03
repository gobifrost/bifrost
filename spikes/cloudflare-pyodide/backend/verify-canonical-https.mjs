import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, mkdtempSync, rmSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const read = path => readFileSync(new URL(path, import.meta.url));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const receipt = JSON.parse(read('../results/canonical-https/v2.json'));
for (const name of ['ct128-canonical-failure.py', 'shared-native-pool.py']) {
  const bytes = name === 'shared-native-pool.py'
    ? execFileSync('git', ['show', '338eac91a8ca72bd38ab479c9d8fb200f5f8ae27:spikes/cloudflare-pyodide/backend/' + name]) : read(name);
  assert.equal(hash(bytes), receipt.packet_sha256[name], name);
}
for (const name of ['canonical-https-observer.mjs', 'approved-https-candidate.mjs', 'attempt-egress.mjs']) {
  assert.equal(hash(read(name)), receipt.packet_sha256[`project/backend/${name}`], name);
}
const entry = read('../results/canonical-https/entry.mjs');
assert.equal(hash(entry), receipt.packet_sha256['project/backend/authorized-local-entry.mjs']);
const temporary = mkdtempSync(join(tmpdir(), 'canonical-https-evidence-'));
try {
  const output = join(temporary, 'entry.mjs');
  const generator = join(temporary, 'prepare.mjs');
  writeFileSync(generator, execFileSync('git', ['show', '338eac91a8ca72bd38ab479c9d8fb200f5f8ae27:spikes/cloudflare-pyodide/backend/prepare-canonical-https.mjs']));
  writeFileSync(join(temporary, 'authorized-local-entry.mjs'), read('authorized-local-entry.mjs'));
  execFileSync(process.execPath, [generator, output]);
  assert.deepEqual(readFileSync(output), entry);
  const patch = join(temporary, 'patch.mjs');
  execFileSync(process.execPath, [fileURLToPath(new URL('stage-httpx-redirect.mjs', import.meta.url)), patch]);
  assert.equal(hash(readFileSync(patch)), receipt.packet_sha256['project/backend/httpx-redirect-code.mjs']);
} finally {rmSync(temporary, {recursive: true, force: true});}
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.equal(receipt.physical_termination_verified, false);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.staged_runtime_provenance.bundle_sha256, '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731');
assert.ok(receipt.commands.every(command => command.exit_code === 0));
const proof = receipt.shared_pool_result;
assert.deepEqual(Object.values(proof.launches), [1, 1]);
assert.deepEqual(proof.native_busy_counts, [2, 2, 1, 0]);
for (const field of ['stale_terminal_owner_rejected', 'duplicate_dispatch_reused_peer',
  'lost_ack_reconnect_verified', 'failed_replay_unchanged_verified',
  'canonical_terminal_reconciliation_verified', 'fixture_worker_cascade_cleanup_verified',
  'mux_retired_after_canonical_ack']) assert.equal(proof[field], true, field);
assert.deepEqual(Object.values(proof.attempts).map(value => value.canonical_status).sort(), ['Failed', 'Success']);
for (const attempt of Object.values(proof.attempts)) {
  assert.equal(attempt.access_after_terminal_status, 401);
  assert.equal(attempt.evidence.runtime_cleanup.sdk_close_returned, true);
  assert.ok(attempt.persisted_logs.some(message => message.includes('synthetic_https_qualified')));
}
assert.deepEqual(receipt.synthetic_https_effects.map(effect => `${effect.body}:${effect.path}`).sort(),
  ['fails-first:/redirect', 'fails-first:/write', 'succeeds-second:/redirect', 'succeeds-second:/write']);
assert.ok(receipt.synthetic_https_effects.every(effect => effect.vendor_authorization_matches));
console.log('Actual local canonical engine/SDK plus synthetic HTTPS verified; admission/cloud/termination unqualified.');
