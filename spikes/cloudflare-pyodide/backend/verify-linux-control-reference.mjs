// Offline verification of retained guest evidence; does not run an executor.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

for (const [kind, digest] of Object.entries({
  bootstrap: '8bbb2dfda6427b3aff360aaa26d08afb25d1e1f6db511468f6193524c1bef51f',
  reference: '12b6c9593feedd49693b06add49ab5c418d8a866cfae6caec194cf2cb833280b',
})) {
  const bytes = readFileSync(new URL(`../results/linux-control-reference/${kind}-receipt.json`, import.meta.url));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), digest);
  const report = JSON.parse(bytes);
  assert.equal(report.source, 'f417d03f5c8d5e3a182e68f69e2bf601a3b60125');
  assert.equal(report.qualification_passed, true);
  assert.equal(report.cloud_invocations, 0);
  assert.ok(report.commands.every(command => command.exit_code === 0));
  assert.deepEqual(report.running_after_cleanup, []);
  assert.equal(report.volumes_deleted, false);
  const observed = report.observed;
  assert.equal(observed.pyodide_physical_termination_verified, false);
  assert.equal(observed.native_templates_stopped, true);
  assert.equal(observed.fixture_worker_cleanup_verified, true);
  // This is a historical run: bind its fixture to the tested immutable commit.
  const fixture = execFileSync('git', ['show',
    `c3f5cf807fdbf22330596fca294f064e453c589e:spikes/cloudflare-pyodide/backend/linux-control-${kind}.py`],
  {cwd: fileURLToPath(new URL('../../../', import.meta.url))});
  assert.equal(createHash('sha256').update(fixture).digest('hex'), report.fixture_sha256);
  if (kind === 'bootstrap') {
    assert.equal(observed.inventory_sent, false);
    assert.equal(observed.workflow_invocations, 0);
    assert.equal(observed.execution_admission_qualified, false);
    assert.equal(observed.native_template_pids.length, 2);
  } else {
    assert.equal(observed.terminal_status, 'Success');
    assert.equal(observed.independent_document_value, 70);
    assert.equal(observed.wire_events.workflow_commands, 1);
    assert.equal(observed.wire_events.result_events, 1);
    assert.equal(observed.wire_events.pid_acks.length, 1);
    assert.ok(Number.isInteger(observed.wire_events.pid_acks[0]) && observed.wire_events.pid_acks[0] > 0);
    assert.equal(observed.retained_terminal_acknowledged, true);
    assert.equal(observed.real_linux_fork_verified, true);
    assert.equal(observed.runtime_access_after_terminal_status, 401);
  }
}
console.log('Linux signed-control bootstrap and real SDK reference receipts verified; Pyodide adoption and physical termination remain unqualified.');
