// Offline checks of real guest workerd/API evidence, not deployed acceptance.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const root=new URL('../results/workerd-sdk-resume/',import.meta.url);
for(const [name,hash] of [
  ['warm-2g-receipt.json','e15cd8c56ac1dbe171283b8ad9e391ec1aa3c50f9ce1026b066faf7826417d33'],
  ['cold-2g-receipt.json','1ba364a47c97898e12e1730d823f149e56360795d98301abd6b035039b735702']]) {
  const bytes=readFileSync(new URL(name,root));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),hash);
  const report=JSON.parse(bytes);
  assert.equal(report.runtime,'actual-local-workerd');
  assert.equal(report.qualification_passed,true);
  assert.equal(report.cloudflare_runtime_qualified,false);
  assert.equal(report.physical_termination_verified,false);
  assert.equal(report.cloud_invocations,0);
  assert.deepEqual(report.running_after_cleanup,[]);
  assert.equal(report.provider_state_before_cleanup.OOMKilled,false);
  assert.equal(Number(report.provider_cgroup['memory.max']),2147483648);
  assert.ok(Number(report.provider_cgroup['memory.peak'])>1073741824);
  assert.match(report.provider_cgroup['memory.events'],/^oom 0$/m);
  const run=report.canonical_result;
  for(const key of ['canonical_claim_verified','canonical_terminal_consumption_verified','lost_ack_reconnect_verified','integration_secret_decryption_and_redaction_verified','fixture_worker_cascade_cleanup_verified']) assert.equal(run[key],true,key);
  assert.equal(run.launches,1);
  assert.equal(run.persisted_log_count,2);
  assert.equal(run.independent_document_value,70);
  assert.equal(run.credential_revoked_after_terminal_status,401);
  assert.equal(run.result.result.result.integration_secret_probe,'[REDACTED]');
  assert.deepEqual(run.result.api_calls.filter(c=>c.path==='/api/sdk/integrations/get').map(c=>c.status),[200,403]);
  assert.equal(run.result.api_calls.filter(c=>c.path.endsWith('/documents/upsert')&&c.status===200).length,1);
  if(name.startsWith('cold')) assert.equal(report.credential_free_warmup_exit_code,undefined);
}
for(const name of ['cold-container-state.json','warm-1g-container-state.json']) {
  assert.equal(JSON.parse(readFileSync(new URL(name,root))).OOMKilled,true);
}
const gated=JSON.parse(readFileSync(new URL('warm-1g-receipt.json',root)));
assert.equal(gated.credential_free_warmup_exit_code,1);
assert.equal(gated.commands.length,3);
assert.equal(gated.qualification_passed,false);
console.log('Cold and warm actual-workerd canonical SDK receipts verified; 1 GiB OOM evidence retained, cloud/physical-stop acceptance false.');
