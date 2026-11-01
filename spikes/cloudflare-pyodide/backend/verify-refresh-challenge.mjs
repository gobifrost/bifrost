// Offline failure/success frontier verification. No execution or API access.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const args=process.argv.slice(2);
if(args.length && (args.length!==2 || args[0]!=='--commit' || !/^[a-f0-9]{40}$/.test(args[1]))) {
  throw new Error('Use no arguments or --commit <full tested SHA>');
}
const historicalCommit=args[1],exec=promisify(execFile);
const root=new URL('../../../',import.meta.url);
const read=async name=>JSON.parse(await readFile(new URL('../results/'+name,import.meta.url),'utf8'));
const failed=await read('canonical-refresh-challenge.json');
assert.equal(failed.process_exit_code,1);
assert.equal(failed.result.preflight_challenge_status,401);
assert.equal(failed.result.workflow_document_status,404);
assert.equal(failed.result.provider_evidence.result.error_type,'WorkflowLoadError');
assert.match(failed.result.provider_evidence.result.error,/can't start new thread/);
assert.equal(failed.result.provider_evidence.api_calls.length,1);
assert.equal(failed.result.provider_evidence.api_calls[0].status,401);
const receipt=await read('canonical-refresh-challenge-adapted.json');
assert.equal(receipt.process_exit_code,0);assert.equal(receipt.scenario,'challenge');
assert.notEqual(receipt.unknown_process_state,true);assert.equal(receipt.cloud_invocations,0);
assert.equal(receipt.physical_termination_verified,false);
const r=receipt.result;
for(const field of ['expired_access_probe','canonical_claim_verified','canonical_terminal_consumption_verified',
  'lost_ack_reconnect_verified','fixture_worker_cascade_cleanup_verified']) assert.equal(r[field],true,field);
assert.equal(r.preflight_challenge_status,401);assert.match(r.preflight_challenge_header,/refresh_required/);
assert.equal(r.launches,1);assert.equal(r.credential_revoked_after_terminal_status,401);
assert.equal(r.refresh_revoked_after_terminal_status,401);assert.equal(r.cross_tenant_status,403);
assert.equal(r.independent_document_value,70);
assert.equal(r.result.result.result.value,70);
assert.equal(r.result.result.result.parent_secret_present,false);
assert.equal(r.result.result.result.credential_redaction_probe,'[REDACTED]');
const [challenge,refresh,replay]=r.result.api_calls;
assert.equal(challenge.method,'GET');assert.match(challenge.path,/^\/api\/sdk\/modules\//);assert.equal(challenge.status,401);
assert.deepEqual(refresh,{method:'POST',path:'/auth/refresh',status:200});
assert.deepEqual(replay,{method:'GET',path:challenge.path,status:200});
for(const [path,digest] of Object.entries(receipt.source_sha256)) {
  const bytes=historicalCommit ? (await exec('git',['show',`${historicalCommit}:${path}`],
    {cwd:root,encoding:'buffer',maxBuffer:2*1024*1024})).stdout : await readFile(new URL(path,root));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),digest,path);
}
const manifest=await read('refresh-runtime-manifest.json');
assert.equal(manifest.payload_sha256,receipt.payload_sha256);
for(const [source,module] of [['runtime-runner.py','runtime_runner.py'],['pyodide_refresh.py','pyodide_refresh.py']]) {
  assert.equal(manifest.files[module],receipt.source_sha256['spikes/cloudflare-pyodide/backend/'+source]);
}
console.log('Actual expired-token challenge, native sync bridge, replay, canonical completion and revocation verified; no cloud/physical-stop acceptance');
