import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const receipt=JSON.parse(readFileSync(new URL('../results/cpu-egress/v1.json',import.meta.url)));
const hash=value=>createHash('sha256').update(value).digest('hex');
for(const file of ['cpu-egress-entry.mjs','attempt-egress.mjs','http-context-probe.mjs'])
  assert.equal(hash(readFileSync(new URL(file,import.meta.url))),receipt.packet_sha256[`project/backend/${file}`]);
// Preserve the exact CPU experiment helper before adding the HTTPS probe mode.
assert.equal(hash(execFileSync('git',['show','4919b7feeb66a13d7565dbb81f36c477ea3f2cca:spikes/cloudflare-pyodide/backend/ct128-sdk-probe.py'])),receipt.packet_sha256['ct128-sdk-probe.py']);
assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.engine_sdk_execution_verified,false);
assert.equal(receipt.running_after_cleanup,'false');
assert.equal(receipt.provider_state_before_cleanup.OOMKilled,false);
assert.equal(receipt.observation.status,200);
const {control,cpu,physical_termination_verified}=receipt.observation.body;
assert.equal(physical_termination_verified,false);
assert.deepEqual(control.body,{total:0,effect_status:200});
assert.deepEqual(control.observer.events,['started','after']);
assert.ok(control.effect_attempt_elapsed_ms<control.diagnostic_deadline_ms);
assert.deepEqual(cpu.body,{total:199999990000000,effect_status:410});
assert.deepEqual(cpu.observer.events,['started']);
assert.ok(cpu.effect_attempt_elapsed_ms>=cpu.diagnostic_deadline_ms);
console.log('Actual finite CPU continuation with refused post-deadline effect verified; no physical/provider termination claim.');
