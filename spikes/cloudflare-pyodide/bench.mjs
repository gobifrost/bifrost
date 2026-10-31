// Fixed fixtures only. Never records request credentials.
import {writeFile} from 'node:fs/promises';
import {performance} from 'node:perf_hooks';

const base = process.env.SPIKE_URL ?? 'http://127.0.0.1:8793';
const cases = ['json_transform', 'async_context', 'workspace_import', 'http_orchestration',
  'integration_read', 'table_update', 'multi_step', 'sync_json', 'io_timeout',
  'cpu_timeout', 'secret_probe', 'state_write', 'state_read'];
const results = [];
async function invoke(workload, parameters = {}) {
  const start = performance.now();
  try {
    const response = await fetch(new URL('/'+workload, base), {method: 'POST',
      headers: {'Content-Type': 'application/json', ...(process.env.SPIKE_TOKEN ? {'X-Spike-Token': process.env.SPIKE_TOKEN} : {}), ...(process.env.SPIKE_AUTH ? {'X-Spike-Auth': process.env.SPIKE_AUTH} : {})},
      body: JSON.stringify({workload, parameters}), signal: AbortSignal.timeout(30000)});
    const text = await response.text();
    let body;
    try { body = JSON.parse(text); } catch { body = {non_json: text.slice(0, 1500)}; }
    return {workload, http_status: response.status, client_wall_ms: performance.now()-start, body};
  } catch (error) { return {workload, client_wall_ms: performance.now()-start, error: String(error)}; }
}
for (const workload of cases) {
  results.push(await invoke(workload));
}
results.push(await invoke('multi_step', {fail: true}));
for (let i=0; i<5; i++) results.push({...await invoke('json_transform'), sample: i, phase: 'warm'});
results.push(...await Promise.all(Array.from({length: 4}, () => invoke('json_transform'))));
const report = {recorded_at: new Date().toISOString(), runtime: process.env.SPIKE_RUNTIME ?? 'local-workerd',
  cpu_ms: null, peak_isolate_memory_bytes: null, production_cold_start_ms: null,
  cost_per_1000: null, results};
await writeFile(process.env.SPIKE_OUTPUT ?? 'results/benchmark.json', JSON.stringify(report, null, 2)+'\n');
console.log(JSON.stringify(results.map(r => ({workload: r.workload, status: r.body?.status ?? r.http_status, error: r.body?.error_type ?? r.error})), null, 2));
if(results.some(r=>r.error)) process.exitCode=1;
