import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/retained-http/', import.meta.url);
const read = name => readFile(new URL(name, base), 'utf8');
const raw = await read('retained-http-v1/receipt.json');
assert.doesNotMatch(raw, /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);
const report = JSON.parse(raw);
assert.equal(report.qualification_passed, true);
assert.ok(report.commands.every(command => command.exit_code === 0));
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.provider_state_before_cleanup.Running, true);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
for (const name of ['retained_http_fault.py', 'test_retained_http_fault.py',
  'shared-native-pool.py', 'ct128-canonical-failure.py', 'private_http_pool.py', 'attempt_journal.py', 'workflow_request_mux.py']) {
  const source = execFileSync('git', ['show', 'e82e81c84bb8c4e010a2c86aa2e2735d04b6af03:spikes/cloudflare-pyodide/backend/' + name]);
  assert.equal(createHash('sha256').update(source).digest('hex'), report.packet_sha256[name], name);
}
const probe = report.shared_pool_result;
for (const field of ['retained_http_preack_loss_verified', 'retained_collector_eof_complete',
  'stale_terminal_owner_rejected', 'failed_replay_unchanged_verified', 'lost_ack_reconnect_verified',
  'canonical_peer_accounting_verified', 'canonical_settled_accounting_verified',
  'terminal_obligations_released', 'mux_retired_after_canonical_ack', 'fixture_worker_cascade_cleanup_verified']) {
  assert.equal(probe[field], true, field);
}
assert.equal(probe.parent_accepted_observed, false);
assert.equal(probe.original_collection_error_type, 'ReadError');
assert.equal(probe.retained_posts, 1);
assert.deepEqual(probe.native_busy_counts, [2, 2, 1, 0]);
assert.equal(probe.failed_access_status, 401);
assert.equal(probe.peer_access_status, 200);
const handles = new Set();
for (const [id, attempt] of Object.entries(probe.attempts)) {
  handles.add(attempt.runtime_handle);
  assert.equal(probe.launches[id], 1);
  assert.equal(attempt.access_after_terminal_status, 401);
  assert.equal(attempt.canonical_status, attempt.label === 'fails-first' ? 'Failed' : 'Success');
  assert.ok(attempt.evidence.api_calls.some(call => call.status === 200 && call.path.includes('/documents/upsert')));
  if (attempt.label === 'fails-first') assert.equal(attempt.accepted_monotonic, null);
  else assert.equal(attempt.canonical_result.value, 78);
}
assert.equal(handles.size, 2);
assert.match(await read('retained-http-quality-v1/unit.txt'), /Ran 49 tests[\s\S]*\bOK\b/);
assert.match(await read('retained-http-quality-v1/quality.txt'), /0 errors, 0 warnings/);
console.log('Real local engine/SDK retained-stream adoption after collector pre-ACK fault, lost canonical ACK, reconnect and cleanup verified. No TCP-disconnect, retainer-process-loss or deployed CPU-stop claim.');
