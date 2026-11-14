// Successful verifier means the counterexample/evidence is intact, not accepted isolation.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {CPU_CASES, classifyCpuPlacement} from '../../backend/cpu-placement.mjs';

const root = new URL('../../', import.meta.url);
const results = new URL('results/cpu-placement-cloud/', root);
const json = async name => JSON.parse(await readFile(new URL(name, results), 'utf8'));
const hashes = await json('source.json');
for (const [path, digest] of Object.entries(hashes)) {
  const relative = path.replace(/^spikes\/cloudflare-pyodide\//, '');
  assert.equal(createHash('sha256').update(await readFile(new URL(relative, root))).digest('hex'), digest, path);
}
const open = await json('open-version.json');
const build = await json('build-readback.json');
assert.equal(open.id, 'cd139623-a0a5-4374-b70f-176058397f50');
assert.equal(open.limits.cpu_ms, 1000);
assert.equal(open.guard_binding_present, true);
const deployed = await readFile(new URL('deployed-entry.txt', results));
assert.equal(createHash('sha256').update(deployed).digest('hex'), open.module_hashes[0].sha256);
assert.equal(build.local_build_entry_sha256, open.module_hashes[0].sha256);
assert.equal(build.provider_entry_sha256, open.module_hashes[0].sha256);

const report = await json('report.json');
assert.equal(report.completed, true);
assert.equal(report.canonical_dispatch_verified, false);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.unsupported_dispatch_status, 422);
assert.deepEqual(report.invalid_identity, {phase: 'observer-auth', observer_status: 401});
assert.equal(report.cross_tenant_log_read_status, 403);
assert.equal(report.cases.length, 3);
const findings = [];
for (const [index, name] of CPU_CASES.entries()) {
  const row = report.cases[index];
  assert.equal(row.case_id, name);
  assert.equal(row.http_status, 200);
  const result = row.outcome.outcome;
  assert.equal(result.status, 200);
  assert.deepEqual(result.body.abi, {python: [3, 14, 2], pyodide: '314.0.6', soabi: 'cpython-314-wasm32-emscripten'});
  const prefix = `cpu-placement:${name}:`;
  const expected = name === 'completion' ? ['before', 'after'] : ['before', 'caught', 'after'];
  assert.deepEqual(row.independent_messages, ['cpu-placement:controller-control', prefix + 'parent-preflight',
    ...expected.map(event => prefix + event)]);
  if (name === 'completion') assert.equal(result.body.completed, true);
  else assert.equal(result.body.caught, 'CpuLimitExceeded');
  findings.push(classifyCpuPlacement(name, expected));
}
assert.deepEqual(report.cleanup.ended_access_statuses, [401, 401, 401]);
assert.deepEqual(report.cleanup.ended_refresh_statuses, [401, 401, 401]);
assert.equal(report.cleanup.owned_log_streams_deleted, 3);
const reservations = (await readFile(new URL('invocations.jsonl', results), 'utf8')).trim().split('\n').map(JSON.parse);
assert.deepEqual(reservations.map(row => row.case), ['unsupported', 'invalid_identity', ...CPU_CASES]);
assert.deepEqual(reservations.map(row => row.reservation), [1, 2, 3, 4, 5]);
const cleanup = await json('cleanup.json');
const final = await json('final-version.json');
assert.equal(cleanup.open_version, open.id);
assert.equal(cleanup.final_version, final.id);
assert.equal(final.limits.cpu_ms, 1000);
assert.equal(final.env.DEV_EXPIRES_AT.text, '0');
assert.deepEqual(final.urls, []);
assert.ok(!final.bindings.some(binding => binding.name === 'DEV_AUTH'));
assert.deepEqual(cleanup.secret_names, []);
assert.equal(cleanup.retired_guard_public_status, 404);
assert.equal(cleanup.execution_rows_remaining, 0);
assert.equal(cleanup.controller_running, false);
assert.equal(cleanup.fresh_children, 3);
assert.equal(cleanup.reservation_count, 5);
assert.equal(cleanup.cleanup_get_requests, 1);
console.log(JSON.stringify({evidence_verified: true, findings,
  untrusted_workload_isolation_accepted: false, physical_termination_verified: false}));
