import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const source = path => execFileSync('git', ['show', 'dbf854732be5ad3c4b9f79f837edf213e075496f:' + path]);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const base = new URL('../results/request-mux/', import.meta.url);
const bytes = name => readFile(new URL(name, base));
const json = async name => JSON.parse(await bytes(name));
const first = await json('v1.json');
const exposing = await json('fail-first.json');
assert.equal(exposing.exit_code, 1);
assert.equal(exposing.cases, 5);
assert.equal(exposing.failures, 2);
assert.equal(hash(await bytes('before-helper.txt')), exposing.before_helper_sha256);
assert.equal(exposing.before_helper_sha256, first.packet_sha256['workflow_request_mux.py']);
assert.equal(hash(await bytes('before-test.txt')), first.packet_sha256['test_workflow_request_mux.py']);
assert.equal(hash(await bytes('regression-test.txt')), exposing.test_sha256);
const intermediate = await json('v3.json');
assert.equal(hash(await bytes('v3-canonical.txt')), intermediate.packet_sha256['canonical-local.py']);
assert.equal(intermediate.qualification_passed, true);
const final = await json('v4.json');
const quality = await json('quality-checks.json');
assert.equal(quality.unit_tests.count, 9);
for (const name of ['unit_tests', 'ruff', 'pyright']) assert.equal(quality[name].exit_code, 0);
for (const name of ['workflow_request_mux.py', 'test_workflow_request_mux.py',
  'canonical-local.py', 'ct128-canonical-failure.py', 'http_invocation.py',
  'invocation_stream.py', 'attempt_deadline.py', 'task_cleanup.py']) {
  assert.equal(hash(source('spikes/cloudflare-pyodide/backend/' + name)), final.packet_sha256[name], name);
}
for (const [path, digest] of Object.entries(final.api_overlay_sha256)) {
  assert.equal(hash(source(path)), digest, path);
}
for (const report of [first, intermediate, final]) {
  assert.equal(report.qualification_passed, true);
  assert.ok(report.commands.every(command => command.exit_code === 0));
  assert.deepEqual(report.running_after_cleanup, []);
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.physical_termination_verified, false);
  assert.equal(report.provider_state_before_cleanup.Running, true);
  assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
  const run = report.late_settlement_result;
  for (const field of ['request_mux_verified', 'http_invocation_component_verified',
    'canonical_timeout_preserved', 'deadline_obligation_held_until_actual_terminal',
    'late_success_received', 'late_ack_loss_retained_handle', 'actual_terminal_released_handle',
    'no_redispatch_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[field], true, field);
  assert.equal(run.launches, 1);
  assert.equal(run.access_revoked_before_late_result_status, 401);
  assert.equal(run.independent_document_value, 70);
  assert.equal(run.cross_tenant_status, 403);
  assert.equal(run.result.result.result.value, 70);
  assert.equal(run.result.result.result.parent_secret_present, false);
  assert.equal(run.result.result.result.credential_redaction_probe, '[REDACTED]');
  assert.ok(run.dispatch_accepted_monotonic < run.terminal_collected_monotonic);
  assert.ok(run.result.api_calls.some(call => call.path === '/api/sdk/runtime/logs' && call.status === 410));
}
console.log('Historical attempt-scoped request and real single-attempt lifecycle receipts verified. This artifact alone does not qualify shared-pool execution or deployed cancellation.');
