import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {logDeliveryEvidence} from './log-delivery-evidence.mjs';
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8');
const sha = value => createHash('sha256').update(value).digest('hex');
const receipt = JSON.parse(read('post-revocation-logs-v7.json'));
assert.equal(receipt.qualification_passed, true);
assert.equal(receipt.cloud_invocations, 0);
assert.deepEqual(receipt.running_after_cleanup, []);
assert.equal(receipt.packet_sha256['project/backend/log-delivery-evidence.mjs'], sha(read('log-delivery-evidence.mjs')));
const attempt = Object.values(receipt.shared_pool_result.attempts).find(item => item.label === 'fails-first');
assert.equal(attempt.canonical_status, 'Success');
assert.equal(attempt.evidence.runtime_cleanup.sdk_close_returned, true);
assert.equal(attempt.persisted_logs.length, 2);
assert.equal(attempt.evidence.result.logs.length, 4);
const marker = 'engine_cooperative_cancel_suppressed credential=[REDACTED]';
assert.ok(attempt.evidence.result.logs.some(row => row.message === marker));
assert.ok(!attempt.persisted_logs.includes(marker));
assert.deepEqual(attempt.evidence.log_delivery, logDeliveryEvidence(attempt.evidence.result.logs, attempt.evidence.api_calls));
assert.equal(attempt.evidence.log_delivery.unsuccessful_log_requests, 2);
assert.equal(attempt.evidence.log_delivery.automatic_replay_safe, false);
const directory = mkdtempSync(join(tmpdir(), 'verify-post-revocation-'));
try {
  const output = join(directory, 'fixture.py');
  execFileSync(process.execPath, [fileURLToPath(new URL('prepare-engine-cancel-fixture.mjs', import.meta.url)),
    fileURLToPath(new URL('shared-native-pool.py', import.meta.url)), output, '--post-revocation-log']);
  assert.equal(sha(readFileSync(output)), receipt.packet_sha256['shared-native-pool.py']);
} finally {rmSync(directory, {recursive: true, force: true});}
console.log('Actual post-revocation redaction and captured/persisted log gap verified; persistence repair remains unresolved.');
