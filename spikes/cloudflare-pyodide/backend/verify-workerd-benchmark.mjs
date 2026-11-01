// Retained measurements only. No invocation or Cloudflare acceptance implied.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const bytes = readFileSync(new URL('../results/workerd-benchmark/receipt.json', import.meta.url));
assert.equal(hash(bytes), '6e9f41683e7d30fd863f736cb2d0259dface691bb962cc0b18efa450c83ce11e');
const report = JSON.parse(bytes);
assert.equal(report.qualification_passed, true);
assert.equal(report.cloudflare_runtime_qualified, false);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.cloud_invocations, 0);
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.provider_state_before_cleanup.OOMKilled, false);
for (const [name, digest] of Object.entries(report.packet_sha256)) {
  // Historical benchmark fixture, not a claim about a later optional mode.
  const source = execFileSync('git', ['show',
    `4434ffe73dcac497c1f7ce668bb5e77659364faa:spikes/cloudflare-pyodide/backend/${name}`],
  {cwd: fileURLToPath(new URL('../../../', import.meta.url))});
  assert.equal(hash(source), digest);
}
const run = report.canonical_result;
for (const key of ['canonical_claim_verified', 'canonical_terminal_consumption_verified',
  'lost_ack_reconnect_verified', 'fixture_worker_cascade_cleanup_verified']) assert.equal(run[key], true);
assert.equal(run.launches, 1);
assert.equal(run.independent_document_value, 70);
assert.equal(run.credential_revoked_after_terminal_status, 401);
assert.equal(run.result.api_calls.filter(call => call.path.endsWith('/documents/upsert') && call.status === 200).length, 4);
assert.equal(run.result.api_calls.filter(call => call.path === '/api/sdk/integrations/get' && call.status === 200).length, 4);
assert.equal(run.result.api_calls.filter(call => call.path === '/api/sdk/config/get' && call.status === 200).length, 13);
const result = run.result.result.result;
assert.equal(result.benchmark_cpu_ms, null);
assert.equal(result.benchmark_peak_isolate_memory_bytes, null);
assert.equal(result.benchmark_cost_per_1000, null);
assert.deepEqual(result.benchmark.map(row => row.workload),
  ['json_transform', 'http_orchestration', 'integration_read', 'table_update', 'multi_step']);
for (const row of result.benchmark) {
  assert.equal(row.correctness, true);
  assert.equal(row.samples_wall_ms.length, 3);
  assert.ok(row.samples_wall_ms.every(value => Number.isFinite(value) && value >= 0));
  assert.equal(row.median_wall_ms, [...row.samples_wall_ms].sort((a, b) => a - b)[1]);
}
console.log(JSON.stringify(result.benchmark.map(row => ({workload: row.workload, median_wall_ms: row.median_wall_ms}))));
