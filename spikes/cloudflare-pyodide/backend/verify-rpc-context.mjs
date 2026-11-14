import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root = new URL('../results/rpc-context/', import.meta.url);
for (const v of [1, 2]) {
  const read = suffix => readFileSync(new URL(`v${v}${suffix}`, root));
  const receipt = JSON.parse(read('.json'));
  assert.equal(receipt.cloud_invocations, 0);
  assert.equal(receipt.engine_sdk_execution_verified, false);
  assert.equal(receipt.running_after_cleanup, 'false');
  assert.equal('observation' in receipt, false);
  assert.equal(createHash('sha256').update(read('-entry.mjs.txt')).digest('hex'),
    receipt.packet_sha256['project/backend/rpc-context-entry.mjs']);
  assert.match(read('-log.txt').toString(), v === 1
    ? /Entrypoints to dynamically-loaded workers cannot be transferred/
    : /Cannot perform I\/O on behalf of a different request/);
}
for (const v of [3,4]) {
  const receipt=JSON.parse(readFileSync(new URL(`v${v}.json`,root)));
  assert.equal(receipt.running_after_cleanup,'false');
  assert.equal(receipt.cloud_invocations,0);
  assert.equal(receipt.observation.status,200);
  const source=v===3 ? readFileSync(new URL('v3-entry.mjs.txt',root)) : readFileSync(new URL('rpc-context-entry.mjs',import.meta.url));
  assert.equal(createHash('sha256').update(source).digest('hex'),receipt.packet_sha256['project/backend/rpc-context-entry.mjs']);
  const body=receipt.observation.body;
  assert.equal(body.physical_termination_verified,false);
  assert.deepEqual(body.control.body,{value:7});
  assert.equal(body.control.tail.outcome,'ok');
  assert.deepEqual(body.ended.before_release,['started','response_eof','caller_tail']);
  assert.deepEqual(body.ended.events,['started','response_eof','caller_tail','release']);
  assert.equal(body.ended.tail_seen_before_release,true);
  if(v===4) {
    assert.match(readFileSync(new URL('v4-log.txt',root),'utf8'),/Continuations for that request are unlikely to run safely and have been canceled/);
    assert.equal(body.observation_window_ms,300);
    assert.equal(body.retained.tail_seen_before_release,false);
    assert.deepEqual(body.retained.before_release,['started','response_eof']);
    assert.deepEqual(body.retained.events,['started','response_eof','release','after','bridge_returned','caller_tail']);
    assert.equal(body.retained.tail.outcome,'ok');
  }
}
console.log('Request-owned local child and retained-context positive control verified; bounded I/O observation, not physical/cloud termination.');
