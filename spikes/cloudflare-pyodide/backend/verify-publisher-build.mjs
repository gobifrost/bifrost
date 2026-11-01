// Check preserved negative build evidence; this is not a bundle/runtime acceptance test.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../results/publisher-build-resume/',import.meta.url);
const expected=['96d5daf78e309e5a88fe241eae399664952fb68200913001a47be0252637cf80',
  '1ea73a08a114ba449ab91ad9d276f57b43911639feb2d1487f98b69929ac9c25',
  '241703b31d0732d5462c6e8c89e7da913798206b49a1d53576fc8f4a6b996a20',
  '1cdafd59f8ec443ffb4f0f24552a3ea82c8bf7f4ecc7b538b894685f5eb6753f'];
for(let i=0;i<4;i++) {
  const bytes=readFileSync(new URL(`v${i+1}-receipt.json`,root));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),expected[i]);
  const report=JSON.parse(bytes);
  assert.equal(report.publisher_source,'7caf6dee5bfe9af1b7710ddf66222900007af080');
  assert.equal(report.bundle_verified,false);
  assert.equal(report.runtime_qualified,false);
  assert.equal(report.physical_termination_verified,false);
  assert.equal(report.cloud_invocations,0);
  assert.equal(report.supervisor_requests_target_asset,false);
  assert.deepEqual(report.candidates,[]);
  assert.deepEqual(report.running_after_cleanup,[]);
  assert.equal(report.container_state.OOMKilled,false);
  assert.equal(report.exit_code,[37,33,125,33][i]);
}
assert.match(readFileSync(new URL('v1-build.log',root),'utf8'),/patch: command not found/);
assert.match(readFileSync(new URL('v2-build.log',root),'utf8'),/unable to create native thread/);
assert.match(readFileSync(new URL('v3-build.log',root),'utf8'),/already in use/);
assert.match(readFileSync(new URL('v4-build.log',root),'utf8'),/unable to create native thread/);
console.log('Negative publisher-source build evidence verified; no matching bundle or runtime qualification exists.');
