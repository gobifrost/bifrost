import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root = new URL('../results/canonical-failure-repaired/', import.meta.url);
const read = async name => JSON.parse(await readFile(new URL(name, root), 'utf8'));
const report = await read('v4.json');
assert.equal(report.api_source, '1d33150eb376b71f8a81b427920eda1b0447f829');
assert.equal(report.runtime_engine_source, 'f49d4ba99cebd24aa497f1244833b940e640885f');
assert.equal(report.qualification_passed, true);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
assert.ok(report.commands.every(command => command.exit_code === 0));
const run = report.canonical_result;
for (const field of ['workflow_failure_probe', 'canonical_claim_verified', 'canonical_terminal_consumption_verified',
  'lost_ack_reconnect_verified', 'stdout_capture_verified', 'persisted_failure_stdout_redaction_verified',
  'fixture_worker_cascade_cleanup_verified', 'independent_document_absent']) assert.equal(run[field], true, field);
assert.equal(run.canonical_failure_status, 'Failed');
assert.equal(run.canonical_failure_error, 'fixture failure credential=[REDACTED]');
assert.equal(run.launches, 1);
assert.equal(run.credential_revoked_after_terminal_status, 401);
assert.equal(run.refresh_revoked_after_terminal_status, 401);
assert.equal(run.persisted_log_count, 11);
assert.equal(run.persisted_failure_log_messages.filter(message => message === 'stdout credential=[REDACTED]').length, 1);
assert.equal(run.persisted_failure_log_messages.filter(message => message === 'structured credential=[REDACTED]').length, 1);
const envelope = run.result.result;
assert.equal(envelope.success, false);
assert.equal(envelope.error_type, 'ValueError');
assert.equal(envelope.result, null);
assert.ok(!run.result.api_calls.some(call => call.path.includes('/documents/upsert')));
const mappings = {'canonical-local.py': './canonical-local.py', 'task_cleanup.py': './task_cleanup.py',
  'ct128-canonical-failure.py': './ct128-canonical-failure.py', 'project/probe-io.mjs': '../probe-io.mjs',
  'project/cloudflare.config.ts': './canonical-failure-fixture/cloudflare.config.ts',
  'project/wrangler.config.ts': './canonical-failure-fixture/wrangler.config.ts',
  'project/package.json': './canonical-failure-fixture/package.json'};
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
for (const [packet, digest] of Object.entries(report.packet_sha256)) {
  if (packet === 'project/backend/generated-runtime.mjs') {
    assert.equal(digest, 'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
    continue;
  }
  const path = mappings[packet] ?? (packet.startsWith('project/backend/') ? './' + packet.slice('project/backend/'.length) : null);
  assert.ok(path, packet);
  // The packet is historical evidence. Current companion lifetime guards and
  // runner options may change without rewriting the source that was tested.
  const bytes = execFileSync('git', ['show', `80cb0d622:spikes/cloudflare-pyodide/${path.startsWith('../') ? path.slice(3) : 'backend/' + path.slice(2)}`]);
  assert.equal(hash(bytes), digest, packet);
}
for (const name of ['v1.json', 'v2.json']) {
  const prior = await read(name);
  assert.equal(prior.qualification_passed, false);
  assert.equal(prior.commands.length, 3); // Readiness failed before credentialed fixture launch.
  assert.deepEqual(prior.running_after_cleanup, []);
}
console.log('Actual local workerd canonical failure, redacted PostgreSQL stdout, one launch, lost-ACK replay and credential revocation pass. No cloud-dispatch or physical-stop claim.');
