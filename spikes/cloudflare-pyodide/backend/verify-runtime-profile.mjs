import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const base = new URL('../results/runtime-profile/', import.meta.url);
const bytes = name => readFile(new URL(name, base));
const read = async name => JSON.parse(await bytes(name));
const hash = value => createHash('sha256').update(value).digest('hex');
const first = await read('v1.json'), second = await read('v2.json'), final = await read('v3.json');
assert.deepEqual(first.before, {cases: 1, failures: 1, errors: 0, skipped: 0});
assert.deepEqual(first.after, {cases: 72, failures: 0, errors: 0, skipped: 0});
assert.equal(first.commands.at(-1).exit_code, 127);
assert.equal(second.commands.at(-1).exit_code, 1);
assert.equal(final.qualification_passed, true);
assert.ok(final.commands.every(command => command.exit_code === 0));
assert.equal(final.prior_receipt_sha256, hash(await bytes('v1.json')));
assert.equal(final.retained_native_tests_from, 'runtime-profile-v1');
assert.equal(final.live_control_source_changed, true);
assert.deepEqual(final.after, first.after);
for (const report of [first, second, final]) {
  assert.equal(report.api_source, '1d33150eb376b71f8a81b427920eda1b0447f829');
  assert.equal(report.cloud_invocations, 0);
  assert.equal(report.execution_admission_qualified, false);
  assert.deepEqual(report.running_after_cleanup, []);
  for (const name of ['protocol.py', 'host.py', 'test_runtime_profile.py']) assert.equal(report.packet_sha256[name], final.packet_sha256[name], name);
}
const paths = {'protocol.py': '../../../api/src/worker/remote/protocol.py',
  'host.py': '../../../api/src/worker/remote/host.py',
  'test_runtime_profile.py': '../../../api/tests/unit/worker/remote/test_runtime_profile.py',
  'control-auth.py': './control-auth.py', 'ct128-runtime-profile.py': './ct128-runtime-profile.py'};
for (const [name, path] of Object.entries(paths)) {
  assert.equal(hash(await readFile(new URL(path, import.meta.url))), final.packet_sha256[name], name);
}
assert.equal(hash(await bytes('original-runner.txt')), first.packet_sha256['ct128-runtime-profile.py']);
assert.equal(hash(await bytes('original-resume-runner.txt')), second.packet_sha256['ct128-runtime-profile.py']);
for (const report of [first, second]) assert.equal(hash(await bytes('original-control.txt')), report.packet_sha256['control-auth.py']);
assert.ok((await bytes('before.xml')).toString().includes('DID NOT RAISE'));
const live = final.live_control;
for (const field of ['unsupported_signed_profile_close', 'unsigned_profile_close', 'invalid_signature_close',
  'nonce_replay_close', 'superseded_socket_close', 'stale_generation_close']) assert.equal(live[field], 1008, field);
for (const field of ['explicit_signed_linux_profile_accepted', 'unsupported_profiles_preserved_owner_and_claims',
  'empty_handlers_preserved', 'remained_starting', 'linux_templates_unstarted', 'fixture_worker_cleanup_verified']) assert.equal(live[field], true, field);
assert.equal(live.claims_observed, 0);
assert.equal(live.actual_worker_invocations, 0);
assert.equal(live.signed_hello_material_retained, false);
assert.ok(live.signed_reconnect_generation > live.signed_hello_generation);
console.log('Signed runtime-profile boundary: fail-first reproduced, 72 native contracts retained, live Linux/rejection/fencing controls pass. No Pyodide profile, opaque handle or admission enabled.');
