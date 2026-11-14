import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const read = name => JSON.parse(readFileSync(new URL(name, import.meta.url), 'utf8'));
const failed = read('engine-context-v1.json');
const passed = read('engine-context-v2.json');
assert.equal(failed.passed, false);
assert.equal(passed.passed, true);
for (const receipt of [failed, passed]) {
  assert.equal(receipt.cloud_invocations, 0);
  assert.equal(receipt.sdk_operations_verified, false);
  assert.equal(receipt.physical_termination_verified, false);
  assert.equal(receipt.owned_running_after_cleanup, '');
}
assert.deepEqual(passed.cases.map(row => row.mode), ['baseline', 'engine-only', 'engine-context']);
assert.equal(passed.cases[0].body.status, 'Success');
assert.equal(passed.cases[1].body.error_type, 'TypeError');
assert.equal(passed.cases[2].body.status, 'Success');
assert.deepEqual(passed.cases[2].body.result, {value: 28, public_url: 'https://isolated.example.invalid',
  is_agent: true, execution_origin: 'agent', agent_id: 'synthetic-agent',
  original_user_is_platform_admin: true, original_user_id: 'synthetic-original-user'});
for (const [name, expected] of Object.entries(passed.overlay_sha256)) {
  const bytes = execFileSync('git', ['show', `${passed.source}:api/${name}`]);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), expected);
}
console.log('Pinned current engine/context async execution passed locally; not refreshed SDK, cloud or termination acceptance.');
