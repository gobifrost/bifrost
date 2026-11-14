import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/owned-deadline/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const raw = await read('auto-delivery-v2/receipt.json');
assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
const report = JSON.parse(raw);
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.provider_state_before_cleanup.Running, true);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
for (const name of ['private_http_pool.py', 'test_private_http_pool.py', 'attempt_deadline.py', 'test_attempt_deadline.py']) {
  const source = execFileSync('git', ['show', 'c3d401c1900fed226fdb740b90398d91a6b7c907:spikes/cloudflare-pyodide/backend/' + name]);
  assert.equal(createHash('sha256').update(source).digest('hex'), report.packet_sha256[name], name);
}
assert.match(await read('owned-deadline-quality-v1/unit.txt'), /test_original_deadline_reports_before_launch_ack_without_releasing_capacity/);
assert.doesNotMatch(await read('owned-deadline-quality-v1/unit.txt'), /\bOK\b/);
assert.match(await read('owned-deadline-quality-v2/unit.txt'), /Ran 39 tests[\s\S]*\bOK\b/);
assert.match(await read('owned-deadline-quality-v2/quality.txt'), /0 errors, 0 warnings/);
assert.equal(report.shared_pool_result.automatic_terminal_delivery_verified, true);
assert.equal(report.shared_pool_result.failed_replay_unchanged_verified, true);
assert.deepEqual(report.shared_pool_result.native_busy_counts, [2, 2, 1, 0]);
console.log('Original-deadline integration: 39 unit tests and actual normal engine/SDK completion verified. Real private-adapter timeout/late-result qualification remains next.');
