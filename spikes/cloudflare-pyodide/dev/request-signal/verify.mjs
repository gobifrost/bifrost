import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root = new URL('../../', import.meta.url);
const base = new URL('results/request-signal-cloud/', root);
const read = async name => JSON.parse(await readFile(new URL(name, base), 'utf8'));
for (const [path, hash] of Object.entries(await read('source.json'))) {
  assert.equal(createHash('sha256').update(await readFile(new URL(path.replace(/^spikes\/cloudflare-pyodide\//, ''), root))).digest('hex'), hash);
}
const open = await read('open-version.json');
const build = await read('build.json');
assert.ok(open.compatibility_flags.includes('enable_request_signal'));
assert.equal(open.limits.cpu_ms, 1000);
const deployedHash = createHash('sha256').update(await readFile(new URL('deployed-entry.txt', base))).digest('hex');
assert.equal(deployedHash, open.module_hashes[0].sha256);
assert.equal(deployedHash, build.local_build_entry_sha256);
const report = await read('report.json');
assert.equal(report.completed, true);
assert.equal(report.incoming_disconnect_gate_passed, true);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.canonical_dispatch_verified, false);
assert.equal(report.unsupported_dispatch_status, 422);
assert.deepEqual(report.invalid_identity, {phase: 'observer-auth', observer_status: 401});
assert.equal(report.cross_tenant_log_read_status, 403);
assert.deepEqual(report.cases.map(row => row.case_id), ['completion', 'parent_abort', 'io_abort', 'cpu_abort']);
for (const [index, row] of report.cases.entries()) {
  assert.equal(row.client_abort_requested, index > 0);
  const prefix = `signal:${row.case_id}:`;
  const suffix = index === 0 ? ['after'] : index < 3 ? ['parent_abort_received'] : [];
  assert.deepEqual(row.independent_messages, ['signal:controller-control', prefix + 'preflight', prefix + 'before', ...suffix.map(value => prefix + value)]);
}
assert.deepEqual(report.cleanup.ended_access_statuses, [401, 401, 401, 401]);
assert.deepEqual(report.cleanup.ended_refresh_statuses, [401, 401, 401, 401]);
const ledger = (await readFile(new URL('invocations.jsonl', base), 'utf8')).trim().split('\n').map(JSON.parse);
assert.deepEqual(ledger.map(row => row.case), ['unsupported', 'invalid_identity', 'completion', 'parent_abort', 'io_abort', 'cpu_abort']);
const final = await read('final-version.json');
const cleanup = await read('cleanup.json');
assert.equal(cleanup.final_version, final.id);
assert.equal(final.env.DEV_EXPIRES_AT.text, '0');
assert.deepEqual(final.urls, []);
assert.ok(!(final.compatibility_flags ?? []).includes('enable_request_signal'));
assert.ok(!final.bindings.some(binding => binding.name === 'DEV_AUTH'));
assert.deepEqual(cleanup.secret_names, []);
assert.equal(cleanup.retired_guard_get_status, 404);
assert.equal(cleanup.execution_rows_remaining, 0);
assert.equal(cleanup.controller_running, false);
console.log(JSON.stringify({deployed_incoming_disconnect_control_passed: true,
  deployed_io_parent_abort_observed: true, cpu_parent_abort_observed: false,
  physical_termination_verified: false, temporary_configuration_retired: true}));
