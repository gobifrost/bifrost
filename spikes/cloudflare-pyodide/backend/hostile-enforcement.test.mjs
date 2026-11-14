// Hostile enforcement driver (Phase 2): fresh isolate per attempt.
// Local first (LOCAL_PROBE=true, no auth), then ONE isolated cloud worker.
// Usage: node backend/hostile-enforcement.test.mjs [--url http://127.0.0.1:8797] [--out /tmp/...json]
// Cloud: SPIKE_AUTH env must be set; server enforces X-Spike-Auth + expiry.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const args = process.argv.slice(2);
const urlIdx = args.indexOf('--url');
const outIdx = args.indexOf('--out');
const BASE = urlIdx >= 0 ? args[urlIdx + 1] : 'http://127.0.0.1:8797';
const OUT = outIdx >= 0 ? args[outIdx + 1] : null;

const results = [];
const canaries = [];

async function probe(body, auth = process.env.SPIKE_AUTH ?? null) {
  const headers = {'Content-Type': 'application/json'};
  if (auth) headers['X-Spike-Auth'] = auth;
  const started = Date.now();
  const response = await fetch(BASE, {method: 'POST', headers,
    body: JSON.stringify(body), signal: AbortSignal.timeout(90000)});
  const text = await response.text();
  const wallMs = Date.now() - started;
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = {unparsed_len: text.length, unparsed_head: text.slice(0, 1000)}; }
  const record = {request: body, status: response.status, body: parsed, client_wall_ms: wallMs, raw_len: text.length};
  results.push(record);
  return record;
}

function verdict(name, pass, evidence, detail = '') {
  canaries.push({name, pass, evidence, detail});
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  return pass;
}

let failures = 0;
const need = (name, cond, evidence, detail) => {
  if (!verdict(name, !!cond, evidence, detail)) failures++;
};

try {
  // 0. Honest baseline must succeed with before+after and no catch.
  const honest = await probe({action: 'honest', marker: 'baseline'});
  console.log('honest:', JSON.stringify(honest.body).slice(0, 800));
  need('0-honest-baseline', honest.status === 200 && honest.body.outcome?.status === 200 &&
    honest.body.outcome?.body?.total === 499500 &&
    JSON.stringify(honest.body.effects) === JSON.stringify(['before', 'after']),
    honest, JSON.stringify(honest.body.effects));

  // 1. CPU catch-then-effect. Verdict: does after land? If yes, RED (catch-and-continue).
  const p1 = await probe({action: 'cpu_catch_effect', marker: 'cpu-catch-1'});
  console.log('p1 cpu_catch_effect:', JSON.stringify(p1.body).slice(0, 2000));
  const p1eff = p1.body.effects ?? [];
  const p1caught = p1.body.outcome?.body?.caught ?? p1.body.outcome?.error_type ?? null;
  // Record only; RED determination is in the final report (after lands = provider did NOT stop).
  need('1-catch-recorded', p1.status === 200, p1, `effects=${JSON.stringify(p1eff)} caught=${p1caught} wall=${p1.client_wall_ms}ms`);
  console.log(`  P1 detail: status=${p1.status} outcome=${JSON.stringify(p1.body.outcome).slice(0, 800)} effects=${JSON.stringify(p1eff)} wall=${p1.client_wall_ms}ms`);

  // 2. CPU no-catch. Must not be Success with after; must surface as infra error.
  const p2 = await probe({action: 'cpu_nocatch', marker: 'cpu-nocatch-1'});
  console.log('p2 cpu_nocatch:', JSON.stringify(p2.body).slice(0, 2000));
  const p2eff = p2.body.effects ?? [];
  need('2-nocatch-recorded', p2.status === 200, p2, `effects=${JSON.stringify(p2eff)} wall=${p2.client_wall_ms}ms`);
  console.log(`  P2 detail: outcome=${JSON.stringify(p2.body.outcome).slice(0, 1200)} effects=${JSON.stringify(p2eff)} wall=${p2.client_wall_ms}ms`);

  // 3. Memory exhaustion.
  const p3 = await probe({action: 'mem_grow', marker: 'mem-1'});
  console.log('p3 mem_grow:', JSON.stringify(p3.body).slice(0, 2000));
  need('3-mem-recorded', p3.status === 200, p3, `wall=${p3.client_wall_ms}ms`);
  console.log(`  P3 detail: outcome=${JSON.stringify(p3.body.outcome).slice(0, 1200)} effects=${JSON.stringify(p3.body.effects)} wall=${p3.client_wall_ms}ms`);

  // 4a. Subrequest budget abuse.
  const p4a = await probe({action: 'subrequest_abuse', marker: 'subreq-1'});
  console.log('p4a subrequest:', JSON.stringify(p4a.body).slice(0, 3000));
  need('4a-subrequest-recorded', p4a.status === 200, p4a, `wall=${p4a.client_wall_ms}ms`);

  // 4b. Output budget abuse.
  const p4b = await probe({action: 'output_abuse', marker: 'output-1'});
  console.log('p4b output:', JSON.stringify({status: p4b.status, body_keys: Object.keys(p4b.body?.outcome?.body ?? {}), raw_len: p4b.body?.outcome?.raw_len, effects: p4b.body?.effects, wall: p4b.client_wall_ms}).slice(0, 1000));
  need('4b-output-recorded', p4b.status === 200, p4b, `raw_len=${p4b.body?.outcome?.raw_len} wall=${p4b.client_wall_ms}ms`);

  // 5. Normal accounting baseline.
  const p5 = await probe({action: 'normal_accounting', marker: 'acct-1'});
  console.log('p5 accounting:', JSON.stringify(p5.body).slice(0, 800));
  need('5-accounting-recorded', p5.status === 200 && p5.body.outcome?.body?.total === 499500, p5,
    `wall=${p5.client_wall_ms}ms provider_cpu_ms=null(unreported) peak=null(unreported)`);

  console.log(failures === 0 ? 'ENFORCEMENT PROBES: ALL RECORDED' : `ENFORCEMENT PROBES: ${failures} RECORDING FAILURE(S)`);
} catch (error) {
  failures++;
  canaries.push({name: 'harness-error', pass: false, evidence: String(error?.stack ?? error)});
  console.error('HARNESS ERROR:', error);
} finally {
  const redacted = {runtime: 'hostile-enforcement-phase2', base: BASE, canaries, results};
  if (OUT) await writeFile(OUT, JSON.stringify(redacted, null, 2) + '\n');
  if (failures > 0) process.exitCode = 1;
}
