// Verify pinned public source only. This never selects or contacts a guest.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const root=new URL('../../../',import.meta.url);
const r=JSON.parse(await readFile(new URL('../results/remote-test-handoff.json',import.meta.url)));
assert.match(r.source_commit,/^[a-f0-9]{40}$/);
assert.equal(r.source_repository,'https://github.com/MTG-Thomas/bifrost-upstream');
assert.equal(r.status,'prepared_not_executed_remotely');
assert.equal(r.instruction_acted_on,true);
assert.equal(r.target_guest,null);
assert.equal(r.target_owner_lease,null);
assert.equal(r.target_capacity_verified,false);
assert.equal(r.remote_harness_verified,false);
assert.equal(r.remote_execution_observed,false);
assert.equal(r.safe_to_resend,false);
execFileSync('git',['merge-base','--is-ancestor',r.source_commit,'HEAD'],{cwd:root});
for(const [path,digest] of Object.entries(r.source_and_fixture_sha256)) {
  const source=execFileSync('git',['show',`${r.source_commit}:${path}`],{cwd:root,maxBuffer:8*1024*1024});
  assert.equal(createHash('sha256').update(source).digest('hex'),digest,path);
}
console.log('Pinned remote source/fixtures verified; no guest selected or remote execution observed');
