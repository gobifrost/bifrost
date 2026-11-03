import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root=new URL('../results/http-context/',import.meta.url);
const hash=value=>createHash('sha256').update(value).digest('hex');
for(const v of [1,2]) {
  const receipt=JSON.parse(readFileSync(new URL(`v${v}.json`,root)));
  assert.equal(receipt.cloud_invocations,0);
  assert.equal(receipt.engine_sdk_execution_verified,false);
  assert.equal(receipt.running_after_cleanup,'false');
  assert.equal(receipt.observation.status,200);
  assert.equal(hash(execFileSync('git',['show','3770f8cbad4baf396ce9510c73f8d806861a46fe:spikes/cloudflare-pyodide/backend/ct128-sdk-probe.py'])),receipt.packet_sha256['ct128-sdk-probe.py']);
  for(const [file,snapshot] of [['rpc-http-context-entry.mjs','entry'],['http-context-probe.mjs','probe']]) {
    const source=v===1?readFileSync(new URL(`v1-${snapshot}.mjs.txt`,root)):readFileSync(new URL(file,import.meta.url));
    assert.equal(hash(source),receipt.packet_sha256[`project/backend/${file}`]);
  }
  const body=receipt.observation.body;
  assert.equal(body.physical_termination_verified,false);
  assert.equal(body.observation_window_ms,500);
  assert.deepEqual(body.control.body,{value:7});
  assert.equal(body.retained.tail_seen_before_release,false);
  assert.equal(body.retained.before_release.pending_gates,1);
  assert.equal(body.retained.after.pending_gates,0);
  assert.ok(body.retained.after.events.indexOf('after')>body.retained.after.events.indexOf('release'));
  assert.equal(body.ended.tail_seen_before_release,true);
  assert.equal(body.ended.after.events.includes('after'),false);
  for(const name of ['control','retained','ended'])assert.equal(body[name].tail.outcome,'ok');
  if(v===1)assert.equal(body.ended.before_release.events.includes('gate'),false);
  else {
    assert.deepEqual(body.ended.before_release.events,['started','gate','eof','gate_connection_closed']);
    assert.equal(body.ended.before_release.released,false);
    assert.equal(body.ended.before_release.pending_gates,0);
    assert.deepEqual(body.ended.after.events,['started','gate','eof','gate_connection_closed','release']);
  }
}
console.log('Actual local in-flight HTTP close and retained positive control verified; no physical CPU/deployed acceptance.');
