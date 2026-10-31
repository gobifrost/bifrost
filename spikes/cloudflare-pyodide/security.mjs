import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const observations = [];
async function invoke(workload, parameters={}, token) {
  const response = await fetch('http://127.0.0.1:8793', {method:'POST',
    headers:{'Content-Type':'application/json', ...(token ? {'X-Spike-Token':token}: {})},
    body:JSON.stringify({workload, parameters}), signal:AbortSignal.timeout(30000)});
  return {http:response.status, body:await response.json()};
}
await invoke('state_write');
const state = await invoke('state_read');
assert.equal(state.body.result, 'synthetic-execution-A');
observations.push({test:'cross_execution_state', outcome:'incompatible', evidence:state});
const io = await invoke('io_timeout');
assert.equal(io.body.error_type, 'TimeoutError');
observations.push({test:'cooperative_io_cancellation', outcome:'observed', evidence:io});
const cpu = await invoke('cpu_timeout');
assert.equal(cpu.body.status, 'Success');
assert.ok(cpu.body.probe_wall_ms >= 150);
observations.push({test:'cpu_cancellation', outcome:'incompatible', evidence:cpu});
const secret = await invoke('secret_probe');
assert.equal(secret.body.error_message, '[REDACTED]');
assert.ok(!JSON.stringify(secret.body).includes('synthetic-secret-12345'));
observations.push({test:'registered_secret_result_redaction', outcome:'observed', evidence:secret});
const first = await invoke('duplicate_probe');
const second = await invoke('duplicate_probe');
assert.equal(second.body.result, first.body.result+1);
observations.push({test:'duplicate_invocation', outcome:'no_deduplication', evidence:[first,second]});
const oversized = await invoke('json_transform', {padding:'x'.repeat(70000)});
assert.equal(oversized.http, 413);
observations.push({test:'oversized_parameters', outcome:'rejected_after_json_parse', evidence:oversized});
const invalid = await invoke('integration_read', {}, 'synthetic-invalid-token');
observations.push({test:'invalid_credentials', outcome:invalid.body.error_type, evidence:invalid});
await writeFile('results/security.json', JSON.stringify({runtime:'local-workerd', observations},null,2)+'\n');
console.log(observations.map(o=>`${o.test}: ${o.outcome}`).join('\n'));
