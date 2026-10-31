import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const report=JSON.parse(await readFile(process.argv[2] ?? 'results/benchmark.json','utf8'));
const rows=report.results;
const find=name=>rows.find(r=>r.workload===name)?.body;
assert.deepEqual(find('json_transform').result,{count:1000,sum:1498500});
assert.equal(find('async_context').result.value,7);
assert.deepEqual(find('workspace_import').result,{workspace_result:42});
assert.deepEqual(find('http_orchestration').result,{statuses:[200,200,200]});
assert.equal(find('multi_step').status,'Success');
assert.ok(find('multi_step').logs.some(log=>log.message==='structured log probe'));
assert.equal(rows.find(r=>r.workload==='multi_step'&&r.body.status==='Failed').body.error_type,'ValueError');
assert.equal(find('sync_json').error_type,'TypeError');
assert.equal(find('io_timeout').error_type,'TimeoutError');
if(report.runtime==='deployed-cloudflare-python-worker') {
  assert.equal(rows.find(r=>r.workload==='cpu_timeout').http_status,500);
} else assert.equal(find('cpu_timeout').status,'Success');
assert.equal(find('secret_probe').error_message,'[REDACTED]');
assert.ok(!JSON.stringify(find('secret_probe')).includes('synthetic-secret-12345'));
if (process.argv.includes('--sdk')) {
  assert.deepEqual(find('integration_read').result,{found:true,config:{answer:42}});
  assert.deepEqual(find('table_update').result,{written:{value:42},read:{value:42}});
}
const warm=rows.filter(r=>r.phase==='warm').map(r=>r.client_wall_ms).sort((a,b)=>a-b);
const summary={artifact:process.argv[2]??'results/benchmark.json', assertions:'passed',
  warm_json_client_median_ms:warm[Math.floor(warm.length/2)],warm_json_samples:warm,
  correctness:'fixed expected values asserted; negative results are boundaries, not successes'};
await writeFile(report.runtime==='deployed-cloudflare-python-worker' ? 'results/verification-cloud.json' : 'results/verification.json',JSON.stringify(summary,null,2)+'\n');
console.log(JSON.stringify(summary,null,2));
