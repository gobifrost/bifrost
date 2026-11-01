// Verify retained local Linux measurements, not provider deployment acceptance.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const bytes = readFileSync(new URL('../results/linux-benchmark/receipt.json', import.meta.url));
assert.equal(hash(bytes), 'b477d4aee3462ba5c69bf42dc8bf184dc6590ef30845ec8fa02fbec6b8ea50ae');
const report = JSON.parse(bytes);
assert.equal(report.qualification_passed, true);
assert.equal(report.cloud_invocations, 0);
assert.deepEqual(report.running_after_cleanup, []);
assert.equal(report.volumes_deleted, false);
assert.equal(report.fixture_sha256, hash(execFileSync('git', ['show',
  'ca40ad458c29ef465820fa76dd6767811ac5caf6:spikes/cloudflare-pyodide/backend/linux-control-reference.py'],
{cwd: fileURLToPath(new URL('../../../', import.meta.url))})));
assert.equal(report.benchmark_source_sha256, hash(readFileSync(new URL('benchmark-workloads.py', import.meta.url))));
const run = report.observed;
assert.equal(run.real_linux_fork_verified, true);
assert.equal(run.retained_terminal_acknowledged, true);
assert.equal(run.runtime_access_after_terminal_status, 401);
assert.equal(run.independent_document_value, 70);
assert.equal(run.independent_benchmark_documents, 3);
assert.equal(run.native_templates_stopped, true);
assert.equal(run.fixture_worker_cleanup_verified, true);
assert.deepEqual([...run.retained_log_messages].sort(), ['Linux control reference', 'integration secret=[REDACTED]']);
assert.equal(run.pyodide_physical_termination_verified, false);
assert.equal(run.wire_events.workflow_commands, 1);
assert.equal(run.wire_events.result_events, 1);
assert.equal(run.wire_events.pid_acks.length, 1);
assert.ok(Number.isInteger(run.wire_events.pid_acks[0]) && run.wire_events.pid_acks[0] > 0);
assert.deepEqual(run.benchmark.map(row => row.workload),
  ['json_transform', 'http_orchestration', 'integration_read', 'table_update', 'multi_step']);
for (const row of run.benchmark) {
  assert.equal(row.correctness, true);
  assert.equal(row.samples_wall_ms.length, 3);
  assert.ok(row.samples_wall_ms.every(value => Number.isFinite(value) && value >= 0));
  assert.equal(row.median_wall_ms, [...row.samples_wall_ms].sort((a, b) => a - b)[1]);
}
console.log(JSON.stringify(run.benchmark.map(row => ({workload: row.workload, median_wall_ms: row.median_wall_ms}))));
