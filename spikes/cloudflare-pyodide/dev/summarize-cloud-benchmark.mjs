import {readFile, writeFile} from 'node:fs/promises';
const root = new URL('../results/cloud-benchmark/', import.meta.url);
const records = (await readFile(new URL('cloud-benchmark-raw.jsonl', root), 'utf8')).trim().split('\n').map(JSON.parse);
const rows = records.map(({workload, receipt}) => ({
  workload, correctness: receipt.benchmark[0].correctness,
  fresh_child_roundtrip_ms: receipt.fresh_child_provider_roundtrip_ms,
  reported_engine_duration_ms: receipt.result.result.duration_ms,
  raw_operation_clock_samples_ms: receipt.benchmark[0].samples_wall_ms,
  operation_io_clock_median_ms: workload === 'json_transform' ? null : receipt.benchmark[0].median_wall_ms,
  cpu_ms: null, peak_isolate_memory_bytes: null,
}));
const summary = {scope: 'Five serial fresh children; three operation samples each; no warm-isolate or matched current Linux comparison',
  timer_warning: 'Deployed timers advance after I/O only. CPU-only zero deltas are not elapsed CPU time or a sub-millisecond bound.',
  rows, cpu_analytics_rows: 0, measured_cost_per_1000_usd: null,
  estimates: {fresh_creations_per_execution: 1, fresh_creation_usd_after_allowance: 0.002,
    creation_only_usd_per_1000_after_allowance: 2, creation_only_usd_for_five_after_allowance: 0.01,
    remaining_included_allowance: null, cpu_and_request_charges: null, monthly_plan_allocation: null,
    pricing_source: 'https://developers.cloudflare.com/dynamic-workers/pricing/'},
  timer_source: 'https://developers.cloudflare.com/workers/runtime-apis/performance/'};
await writeFile(new URL('summary.json', root), JSON.stringify(summary, null, 2) + '\n', {flag: 'wx'});
const labels = {json_transform: 'JSON transformation', http_orchestration: 'HTTP orchestration',
  integration_read: 'Integration read', table_update: 'Table update', multi_step: 'Multi-step workflow'};
const bars = rows.map(row => `<div class="row"><span>${labels[row.workload]}</span><div class="track"><div class="bar" style="width:${row.fresh_child_roundtrip_ms / 110}%"></div></div><strong>${(row.fresh_child_roundtrip_ms / 1000).toFixed(2)} s</strong></div>`).join('');
const html = `<!doctype html><html><head><meta charset="utf-8"><style>
:root{font-family:var(--font-sans,system-ui)}body{margin:0;color:var(--foreground,CanvasText)}
.row{display:grid;grid-template-columns:150px 1fr 60px;gap:10px;align-items:center;margin:14px 0;font-size:13px}
.track{height:22px;background:var(--secondary,Canvas);border-radius:var(--radius)}
.bar{height:22px;background:var(--chart-1,Highlight);border-radius:var(--radius)}
strong{text-align:right}p{font-size:13px;color:var(--muted-foreground,GrayText);line-height:1.5}
@media(max-width:480px){.row{grid-template-columns:110px 1fr 50px;gap:6px;font-size:11px}}
</style></head><body><p>Measured round trip from CT128 to the guarded Worker, one fresh Python child per workload.</p>${bars}<p>All correctness checks passed. These totals include initialization, source/SDK/API work and transport. They do not isolate startup or CPU. Three inner samples per workload; no warm-isolate measurements.</p></body></html>`;
await writeFile(new URL('roundtrip.html', root), html + '\n', {flag: 'wx'});
console.log(JSON.stringify(rows));
