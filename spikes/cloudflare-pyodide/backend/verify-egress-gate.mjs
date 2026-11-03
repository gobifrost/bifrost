import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const base = new URL('../results/egress-gate/', import.meta.url);
const read = async name => JSON.parse(await readFile(new URL(name, base), 'utf8'));
const report = await read('pyodide-v1.json');
assert.equal(report.source_base, 'd65d055a1f49f8633b4e808ebff6a461297249d9');
assert.equal(report.driver_exit_code, 0);
assert.equal(report.cloud_invocations, 0);
assert.equal(report.physical_termination_verified, false);
assert.equal(report.state_before_cleanup.OOMKilled, false);
assert.deepEqual(report.running_after_cleanup, []);
for (const [name, digest] of Object.entries(report.packet_sha256)) {
  const path = ['package.json', 'cloudflare.config.ts', 'wrangler.config.ts'].includes(name) ? './egress-fixture/' + name : './' + name;
  assert.equal(createHash('sha256').update(await readFile(new URL(path, import.meta.url))).digest('hex'), digest, name);
}
const [normal, late] = report.observation.records;
assert.equal(normal.mode, 'completion');
assert.deepEqual(normal.body.statuses, [204]);
assert.equal(normal.events.filter(event => event.event === 'effect').length, 2);
assert.equal(late.mode, 'late');
assert.deepEqual(late.body.statuses, [410, 410, 410]);
assert.equal(late.body.total, 199999990000000);
assert.equal(late.body.continued_after_rejections, true);
assert.equal(late.events.filter(event => event.event === 'effect').length, 1);
assert.ok(late.events.filter(event => event.event === 'after').every(event => event.status === 410));
for (const row of [normal, late]) {
  assert.deepEqual(row.body.abi, {python: [3, 14, 2], pyodide: '314.0.6', soabi: 'cpython-314-wasm32-emscripten'});
}
const failed = await read('canonical-v1.json');
assert.equal(failed.qualification_passed, false);
assert.equal(failed.commands.at(-1).exit_code, 2);
assert.deepEqual(failed.running_after_cleanup, []);
const canonical = await read('canonical-v2.json');
assert.equal(canonical.qualification_passed, true);
assert.equal(canonical.cloud_invocations, 0);
assert.equal(canonical.physical_termination_verified, false);
assert.equal(canonical.api_source, '1d33150eb376b71f8a81b427920eda1b0447f829');
assert.equal(canonical.runtime_engine_source, 'f49d4ba99cebd24aa497f1244833b940e640885f');
assert.deepEqual(canonical.running_after_cleanup, []);
assert.equal(canonical.provider_state_before_cleanup.OOMKilled, false);
assert.ok(canonical.commands.every(command => command.exit_code === 0));
for (const name of ['attempt-egress.mjs', 'authorized-local-entry.mjs', 'ct128-canonical-failure.py']) {
  assert.equal(createHash('sha256').update(await readFile(new URL('./' + name, import.meta.url))).digest('hex'), canonical.packet_sha256[name], name);
}
assert.equal(canonical.packet_sha256['project/backend/attempt-egress.mjs'], canonical.packet_sha256['attempt-egress.mjs']);
assert.equal(canonical.packet_sha256['project/backend/authorized-local-entry.mjs'], canonical.packet_sha256['authorized-local-entry.mjs']);
assert.equal(canonical.packet_sha256['project/backend/generated-runtime.mjs'], 'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
for (const name of ['canonical-local.py', 'task_cleanup.py']) {
  const bytes = execFileSync('git', ['show', `80cb0d622:spikes/cloudflare-pyodide/backend/${name}`]);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), canonical.packet_sha256[name], name);
}
const run = canonical.canonical_result;
for (const name of ['canonical_claim_verified', 'canonical_terminal_consumption_verified', 'lost_ack_reconnect_verified',
  'persisted_failure_stdout_redaction_verified', 'fixture_worker_cascade_cleanup_verified', 'independent_document_absent']) assert.equal(run[name], true, name);
assert.equal(run.launches, 1);
assert.equal(run.canonical_failure_status, 'Failed');
assert.equal(run.persisted_log_count, 11);
assert.equal(run.credential_revoked_after_terminal_status, 401);
assert.equal(run.refresh_revoked_after_terminal_status, 401);
console.log('Local Python late requests refused while computation continues; real engine canonical failure/replay, redaction, credential revocation and cleanup pass. No cloud, physical-stop or in-flight rollback claim.');
