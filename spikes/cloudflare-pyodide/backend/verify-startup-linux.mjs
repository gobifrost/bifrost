import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../results/startup-linux/',import.meta.url);
const read=name=>readFileSync(new URL(name,root),'utf8');
const receipt=JSON.parse(read('v1.json'));
const hash=value=>createHash('sha256').update(value).digest('hex');
for(const file of ['ct128-linux-startup-candidate.py','linux-startup-candidate.py','task_cleanup.py','pyright-guest-fixtures.json'])
  assert.equal(hash(readFileSync(new URL(file,import.meta.url))),receipt.packet_sha256[file]);
assert.equal(hash(readFileSync(new URL('authorized-local.py',import.meta.url))),receipt.packet_sha256['spike/backend/authorized-local.py']);
assert.equal(read('api-source-readback.txt').trim(),'90bf84fb5213856473d04a770bae6ac1d3dfd71eadd0a897b0ac291a6c3cddde');
assert.equal(receipt.packet_sha256['remote_pool.py.txt'],read('api-source-readback.txt').trim());
assert.equal(receipt.qualification_passed,true);
assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.volumes_deleted,false);
assert.deepEqual(receipt.running_after_cleanup,[]);
assert.ok(receipt.commands.every(command=>command.exit_code===0));
const body=receipt.observed;
for(const key of ['qualification_passed','real_linux_fork_verified','retained_terminal_acknowledged','native_templates_stopped',
  'fixture_worker_cleanup_verified','signed_retirement_and_pool_cache_cleanup_verified'])assert.equal(body[key],true);
assert.equal(body.pyodide_physical_termination_verified,false);
assert.equal(body.terminal_status,'Success');
assert.equal(body.independent_document_value,70);
assert.equal(body.runtime_access_after_terminal_status,401);
assert.equal(body.wire_events.configure_commands,1);
assert.equal(body.wire_events.workflow_commands,1);
assert.equal(body.wire_events.result_events,1);
assert.equal(body.wire_events.pid_acks.length,1);
assert.ok(body.wire_events.pid_acks[0]>0);
for(const row of read('container-readback.txt').trim().split('\n')) {
  const [name,image,running,memory,cpu]=row.split('|');
  assert.ok(name.startsWith('/'+receipt.project+'-'));
  assert.match(image,/^sha256:[a-f0-9]{64}$/);
  assert.equal(running,'false');assert.ok(Number(memory)>0);assert.ok(Number(cpu)>0);
}
console.log('Actual signed Linux candidate startup, engine/SDK execution and retirement/cache cleanup verified; no Pyodide admission claim.');
