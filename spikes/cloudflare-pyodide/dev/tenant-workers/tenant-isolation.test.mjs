// Integration: per-tenant persistent workers under local workerd.
// Boot:  npx wrangler dev --config dev/tenant-workers/wrangler.tenant-local.jsonc --port 8811 --ip 127.0.0.1
// Run:    node dev/tenant-workers/tenant-isolation.test.mjs [--url http://127.0.0.1:8811]
//
// Proves: (a) different tenants get different isolates (hostile canary style);
// (b) same-tenant attempts serialize, with within-tenant residue reported as a
// documented limitation; (c) warm latency vs cold first-attempt latency;
// (d) gates reject undeclared modules/egress before effects.
import assert from 'node:assert/strict';

const args = process.argv.slice(2);
const urlIdx = args.indexOf('--url');
const BASE = urlIdx >= 0 ? args[urlIdx + 1] : 'http://127.0.0.1:8811';

let failures = 0;
function verdict(name, pass, detail = '') {
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  if (!pass) failures++;
}

async function post(body) {
  const t0 = Date.now();
  const response = await fetch(BASE, {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body), signal: AbortSignal.timeout(120000)});
  const parsed = await response.json();
  return {status: response.status, body: parsed, wallMs: Date.now() - t0};
}

const ATTEMPT = `t${Date.now().toString(36)}`;
const tenantA = `tenant-A-${ATTEMPT}`;
const tenantB = `tenant-B-${ATTEMPT}`;
const tenantD = `tenant-serial-${ATTEMPT}`;
const tenantE = `tenant-latency-${ATTEMPT}`;
const tenantF = `tenant-gate-${ATTEMPT}`;
const tenantG = `tenant-egress-${ATTEMPT}`;
const tenantH = `tenant-audit-${ATTEMPT}`;

try {
  // (a) Cross-tenant isolation, hostile-canary style.
  const plantA = await post({tenant_id: tenantA, action: 'plant_builtins', marker: 'marker-A'});
  assert.equal(plantA.status, 200);
  const readB = await post({tenant_id: tenantB, action: 'read_builtins'});
  assert.equal(readB.status, 200);
  verdict('a-cross-tenant-builtins', readB.body.output.builtins == null,
    JSON.stringify(readB.body.output));

  const plantB = await post({tenant_id: tenantB, action: 'plant_module', marker: 'marker-B'});
  assert.equal(plantB.status, 200);
  const readAmod = await post({tenant_id: tenantA, action: 'read_module'});
  assert.equal(readAmod.status, 200);
  verdict('a-cross-tenant-module', readAmod.body.output.present === false,
    JSON.stringify(readAmod.body.output));

  // A is unaffected by B's plant: still exactly A's own marker.
  const readAagain = await post({tenant_id: tenantA, action: 'read_builtins'});
  verdict('a-tenant-A-unaffected', readAagain.body.output.builtins === 'marker-A',
    JSON.stringify(readAagain.body.output));

  // (b) Same-tenant residue: honestly reported as a documented limitation
  // (persistent worker, NOT a fresh isolate like Phase-1 LOADER.load).
  const readAres = await post({tenant_id: tenantA, action: 'read_builtins'});
  verdict('b-same-tenant-residue-documented', readAres.body.output.builtins === 'marker-A',
    `expected residue visible within one tenant: ${JSON.stringify(readAres.body.output)}`);

  // (b) Serialization: two concurrent slow attempts on one tenant must not interleave.
  const slowMs = 500;
  const [s1, s2] = await Promise.all([
    post({tenant_id: tenantD, action: 'slow', marker: 'first', ms: slowMs}),
    post({tenant_id: tenantD, action: 'slow', marker: 'second', ms: slowMs}),
  ]);
  assert.equal(s1.status, 200);
  assert.equal(s2.status, 200);
  const r1 = s1.body.output;
  const r2 = s2.body.output;
  const noOverlap = r1.ended_at <= r2.started_at || r2.ended_at <= r1.started_at;
  verdict('b-serialized-no-interleave', noOverlap, JSON.stringify({r1, r2}));
  const waits = [s1.body.queue_wait_ms, s2.body.queue_wait_ms].sort((x, y) => x - y);
  verdict('b-queue-wait-recorded', waits[1] >= slowMs * 0.8,
    `waits=${JSON.stringify(waits)} (second waiter should queue ~${slowMs}ms)`);
  // Different tenants overlap: pre-warm the parallel tenant so its cold start
  // does not confound the wall measurement, then run both slows at once.
  const tenantI = `tenant-parallel-${ATTEMPT}`;
  await post({tenant_id: tenantI, action: 'honest'});
  await post({tenant_id: tenantD, action: 'honest'});
  const t0 = Date.now();
  await Promise.all([
    post({tenant_id: tenantD, action: 'slow', marker: 'third', ms: slowMs}),
    post({tenant_id: tenantI, action: 'slow', marker: 'parallel', ms: slowMs}),
  ]);
  verdict('b-cross-tenant-parallel', Date.now() - t0 < slowMs * 1.8,
    `wall=${Date.now() - t0}ms for 2x${slowMs}ms across tenants`);

  // (c) Cold first attempt vs warm reuse on the same tenant.
  const cold = await post({tenant_id: tenantE, action: 'honest'});
  const warm = await post({tenant_id: tenantE, action: 'honest'});
  assert.equal(cold.status, 200);
  assert.equal(warm.status, 200);
  console.log(`COLD_MS=${cold.wallMs} WARM_MS=${warm.wallMs} cold_reused=${cold.body.worker_reused} warm_reused=${warm.body.worker_reused}`);
  verdict('c-warm-faster-than-cold', warm.wallMs < cold.wallMs,
    `cold=${cold.wallMs}ms warm=${warm.wallMs}ms`);

  // (d) Gates reject before effects: capability.
  const capReject = await post({tenant_id: tenantF, action: 'run',
    modules: {wf: 'X=1'}, entry: 'wf',
    requirements: {modules: ['wf', 'ghost-module']}, manifest: {wf: 'hash'}});
  verdict('d-capability-rejects-before-effects',
    capReject.status === 422 && capReject.body.phase === 'capability-rejected',
    JSON.stringify(capReject.body));
  const afterCap = await post({tenant_id: tenantF, action: 'honest'});
  verdict('d-rejected-workflow-never-ran',
    afterCap.status === 200 && afterCap.body.output.clean.builtins == null,
    JSON.stringify(afterCap.body.output));

  // (d) Gates reject before effects: egress.
  const egReject = await post({tenant_id: tenantG, action: 'honest',
    url: 'https://evil.example/collect', egress: {hosts: ['example.com']}});
  verdict('d-egress-rejects-before-effects',
    egReject.status === 422 && egReject.body.phase === 'egress-rejected',
    JSON.stringify(egReject.body));

  // (d) Import audit denies undeclared workspace imports post-hoc (effects may exist).
  const wfSrc = 'import helper\ndef main():\n    return {"ok": True}\n';
  const deny = await post({tenant_id: tenantH, action: 'run',
    modules: {wf: wfSrc, helper: 'X = 1\n'}, entry: 'wf',
    requirements: {modules: ['wf']}, manifest: {wf: 'h1', helper: 'h2'}});
  verdict('d-undeclared-import-terminal',
    deny.status === 502 && deny.body.phase === 'undeclared-import' &&
    (deny.body.provider_outcome.undeclared_modules ?? []).includes('helper'),
    JSON.stringify(deny.body));
  // Declared run succeeds and reports its audit trail.
  const okRun = await post({tenant_id: tenantH, action: 'run',
    modules: {wf: 'def main():\n    return {"ok": True}\n'}, entry: 'wf',
    requirements: {modules: ['wf']}, manifest: {wf: 'h1'}});
  verdict('d-declared-run-succeeds',
    okRun.status === 200 && okRun.body.output.result.ok === true,
    JSON.stringify(okRun.body));

  // Child holds no credentials: no token-shaped env, dummy never lands in env.
  const envCheck = await post({tenant_id: tenantB, action: 'env_check'});
  verdict('d-child-holds-no-credentials',
    envCheck.status === 200 &&
    envCheck.body.output.token_shaped_present === false &&
    envCheck.body.output.dummy_credential_present === false,
    JSON.stringify(envCheck.body.output));

  // Generic code digest is stable and tenant-independent.
  const digestRes = await fetch(`${BASE}/__code_digest`);
  const digestBody = await digestRes.json();
  verdict('code-digest-exposed', /^[0-9a-f]{64}$/.test(digestBody.generic_code_sha256),
    JSON.stringify(digestBody));

  console.log(failures === 0
    ? 'TENANT ISOLATION: ALL CHECKS PASS'
    : `TENANT ISOLATION: ${failures} CHECK(S) FAIL`);
} catch (error) {
  failures++;
  console.error('HARNESS ERROR:', error);
}
if (failures > 0) process.exitCode = 1;
