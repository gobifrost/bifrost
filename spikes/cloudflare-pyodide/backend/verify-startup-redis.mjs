import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../results/startup-redis/',import.meta.url);
const read=name=>readFileSync(new URL(name,root));
const hash=value=>createHash('sha256').update(value).digest('hex');
const first=JSON.parse(read('v1.json'));
assert.equal('result' in first,false);
assert.equal(hash(read('v1-supervisor.py.txt')),first.packet_sha256['ct128-startup-redis.py']);
for(const v of [2,3]) {
  const receipt=JSON.parse(read(`v${v}-receipt.json`));
  for(const file of ['startup-redis-probe.py','ct128-startup-redis.py']) {
    const source=v===2?read(`v2-${file}.txt`):readFileSync(new URL(file,import.meta.url));
    assert.equal(hash(source),receipt.packet_sha256[file]);
  }
  assert.equal(receipt.exit_code,0);
  assert.equal(receipt.cloud_invocations,0);
  for(const cleanup of Object.values(receipt.cleanup))assert.ok(['absent','false'].includes(cleanup.running_after));
  const result=receipt.result;
  assert.deepEqual(result,JSON.parse(read(`v${v}-redis-result.json`)));
  assert.equal(result.candidate_sha256,'90bf84fb5213856473d04a770bae6ac1d3dfd71eadd0a897b0ac291a6c3cddde');
  assert.equal(result.signed_startup_verified,false);
  assert.equal(result.execution_admission_qualified,false);
  assert.deepEqual(result.keys_remaining,[]);
  assert.deepEqual(result.records.map(record=>record.mode),['healthy','tagging','delete_failure','readback_failure','cancel','superseded']);
  for(const record of result.records)assert.equal(record.final_fixture_key_exists,0);
  const cases=Object.fromEntries(result.records.map(record=>[record.mode,record]));
  assert.equal(cases.healthy.row_after_first_outcome.runtime,'thin_remote');
  assert.equal(cases.delete_failure.row_after_first_outcome.status,'online');
  assert.deepEqual(cases.delete_failure.row_after_cleanup_retry,{});
  assert.deepEqual(cases.readback_failure.row_after_first_outcome,{});
  assert.equal(cases.superseded.row_after_cleanup_retry.owner_marker,'replacement');
  assert.equal(cases.cancel.error_type,'CancelledError');
}
assert.match(read('v2-pyright.txt').toString(),/2 errors/);
assert.match(read('v3-pyright.txt').toString(),/0 errors, 0 warnings/);
assert.match(read('v3-ruff.txt').toString(),/All checks passed/);
console.log('Six actual Redis candidate cases, source lineage and cleanup verified; no signed startup or Pyodide admission claim.');
