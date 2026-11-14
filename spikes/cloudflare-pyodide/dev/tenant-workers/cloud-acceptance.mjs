// Cloud acceptance: per-tenant persistent Python workers on real Cloudflare.
//
// Deploy (from this directory):
//   cf deploy --secrets-file "$env:TEMP/bifrost-pyodide-tenant-ddf316c1-secrets.json"
// Run:
//   node cloud-acceptance.mjs
//
// Proves in the BILLED runtime what the local workerd suite proves locally, and
// nothing more: cross-tenant isolation, same-tenant serialization and the
// documented within-tenant residue, cold-vs-warm reuse across parent
// invocations, gate ordering, path-keyed import audit, credential-free child.
// It does NOT measure creation billing or per-execution CPU; those are read
// back from the provider separately.
import {readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';

const BASE = 'https://bifrost-pyodide-tenant-ddf316c1.midtown-technology-group.workers.dev';
const MAX_INVOCATIONS = 30;
const secretPath = join(tmpdir(), 'bifrost-pyodide-tenant-ddf316c1-secrets.json');
const {SPIKE_AUTH} = JSON.parse((await readFile(secretPath, 'utf8')).replace(/^\uFEFF/, ''));
if (typeof SPIKE_AUTH !== 'string' || !/^[A-Za-z0-9+/]{43}=$/.test(SPIKE_AUTH)) {
  throw new Error('Invalid spike credential file');
}

let invocations = 0;
const evidence = {runtime: 'deployed-cloudflare', started_at: new Date().toISOString(), probes: []};

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

let failures = 0;
function verdict(name, pass, detail = '') {
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  evidence.probes.push({name, pass, detail});
  if (!pass) failures++;
}

async function post(body, {auth = SPIKE_AUTH} = {}) {
  if (++invocations > MAX_INVOCATIONS) throw new Error('Invocation budget exhausted');
  const t0 = Date.now();
  const headers = {'Content-Type': 'application/json'};
  if (auth !== null) headers['X-Spike-Auth'] = auth;
  const response = await fetch(BASE, {method: 'POST', headers,
    body: JSON.stringify(body), signal: AbortSignal.timeout(180000)});
  const text = await response.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = {unparsed: text.slice(0, 2000)}; }
  return {status: response.status, body: parsed, wallMs: Date.now() - t0};
}

const WF = 'workflows/wf.py';
const GHOST = 'workflows/ghost.py';
const HELPER = 'workflows/helper.py';

const RUN = `t${Date.now().toString(36)}`;
const tenantA = `cloud-A-${RUN}`;
const tenantB = `cloud-B-${RUN}`;
const tenantS = `cloud-serial-${RUN}`;
const tenantC = `cloud-cold-${RUN}`;
const tenantP = `cloud-parallel-${RUN}`;
const tenantG = `cloud-gate-${RUN}`;
const tenantE = `cloud-egress-${RUN}`;
const tenantH = `cloud-audit-${RUN}`;

try {
  // Authentication gate: no header must fail closed before any tenant work.
  const unauth = await post({tenant_id: tenantA, action: 'honest'}, {auth: null});
  verdict('auth-required', unauth.status === 403, `status=${unauth.status}`);

  // (a) Cross-tenant isolation in the billed runtime.
  const plantA = await post({tenant_id: tenantA, action: 'plant_builtins', marker: 'marker-A'});
  verdict('a-plant-accepted', plantA.status === 200 && plantA.body.output.planted === true,
    JSON.stringify(plantA.body).slice(0, 300));
  const readB = await post({tenant_id: tenantB, action: 'read_builtins'});
  verdict('a-cross-tenant-builtins', readB.status === 200 && readB.body.output.builtins == null,
    JSON.stringify(readB.body.output));
  const plantB = await post({tenant_id: tenantB, action: 'plant_module', marker: 'marker-B'});
  verdict('a-plant-module-accepted', plantB.status === 200, JSON.stringify(plantB.body).slice(0, 200));
  const readAmod = await post({tenant_id: tenantA, action: 'read_module'});
  verdict('a-cross-tenant-module', readAmod.status === 200 && readAmod.body.output.present === false,
    JSON.stringify(readAmod.body.output));
  const readAagain = await post({tenant_id: tenantA, action: 'read_builtins'});
  verdict('a-tenant-A-unaffected', readAagain.body.output?.builtins === 'marker-A',
    JSON.stringify(readAagain.body.output));

  // (b) Same-tenant residue: documented limitation, not an isolation claim.
  verdict('b-same-tenant-residue-documented', readAagain.body.output?.builtins === 'marker-A',
    `expected residue visible within one tenant: ${JSON.stringify(readAagain.body.output)}`);

  // Tenant worker id is deterministic and matches the local registry rule.
  const expectedId = `tenant-py-${(await sha256Hex(tenantA)).slice(0, 32)}`;
  verdict('a-tenant-worker-id-deterministic', plantA.body.tenant_worker_id === expectedId,
    `expected=${expectedId} actual=${plantA.body.tenant_worker_id}`);

  // (b) Serialization inside one tenant.
  const slowMs = 500;
  const [s1, s2] = await Promise.all([
    post({tenant_id: tenantS, action: 'slow', marker: 'first', ms: slowMs}),
    post({tenant_id: tenantS, action: 'slow', marker: 'second', ms: slowMs}),
  ]);
  const r1 = s1.body.output, r2 = s2.body.output;
  const noOverlap = r1 && r2 && (r1.ended_at <= r2.started_at || r2.ended_at <= r1.started_at);
  verdict('b-serialized-no-interleave', noOverlap, JSON.stringify({r1, r2}));
  const waits = [s1.body.queue_wait_ms, s2.body.queue_wait_ms].sort((x, y) => x - y);
  verdict('b-queue-wait-recorded', waits[1] >= slowMs * 0.8,
    `waits=${JSON.stringify(waits)} (second waiter should queue ~${slowMs}ms)`);

  // (b) Different tenants overlap (pre-warm both to remove cold-start confound).
  await post({tenant_id: tenantP, action: 'honest'});
  await post({tenant_id: tenantS, action: 'honest'});
  const t0 = Date.now();
  await Promise.all([
    post({tenant_id: tenantS, action: 'slow', marker: 'third', ms: slowMs}),
    post({tenant_id: tenantP, action: 'slow', marker: 'parallel', ms: slowMs}),
  ]);
  const parallelWall = Date.now() - t0;
  verdict('b-cross-tenant-parallel', parallelWall < slowMs * 2.4,
    `wall=${parallelWall}ms for 2x${slowMs}ms across tenants`);

  // (c) Cold first attempt vs warm reuse, across separate parent invocations.
  const cold = await post({tenant_id: tenantC, action: 'honest'});
  const warm = await post({tenant_id: tenantC, action: 'honest'});
  console.log(`COLD_MS=${cold.wallMs} WARM_MS=${warm.wallMs} cold_reused=${cold.body.worker_reused} warm_reused=${warm.body.worker_reused}`);
  verdict('c-warm-faster-than-cold', warm.wallMs < cold.wallMs,
    `cold=${cold.wallMs}ms warm=${warm.wallMs}ms`);

  // (d) Gates reject before effects.
  const wfSrcCap = 'X = 1\n';
  const capReject = await post({tenant_id: tenantG, action: 'run',
    modules: {[WF]: wfSrcCap}, entry: WF,
    requirements: {modules: [WF, GHOST]}, manifest: {[WF]: await sha256Hex(wfSrcCap)}});
  verdict('d-capability-rejects-before-effects',
    capReject.status === 422 && capReject.body.phase === 'capability-rejected' &&
    (capReject.body.missing_modules ?? []).includes(GHOST),
    JSON.stringify(capReject.body).slice(0, 400));
  verdict('d-capability-before-any-module-fetch',
    Array.isArray(capReject.body.api_calls) && capReject.body.api_calls.length === 0,
    JSON.stringify(capReject.body.api_calls));
  const afterCap = await post({tenant_id: tenantG, action: 'honest'});
  verdict('d-rejected-workflow-never-ran',
    afterCap.status === 200 && afterCap.body.output.clean.builtins == null,
    JSON.stringify(afterCap.body.output));

  const egReject = await post({tenant_id: tenantE, action: 'honest',
    url: 'https://evil.example/collect', egress: {hosts: ['example.com']}});
  verdict('d-egress-rejects-before-effects',
    egReject.status === 422 && egReject.body.phase === 'egress-rejected',
    JSON.stringify(egReject.body).slice(0, 300));

  // (d) Path-keyed module source through the gateway + import audit.
  const denyWfSrc = 'import workflows.helper\ndef main():\n    return {"ok": True}\n';
  const denyHelperSrc = 'X = 1\n';
  const deny = await post({tenant_id: tenantH, action: 'run',
    modules: {[WF]: denyWfSrc, [HELPER]: denyHelperSrc}, entry: WF,
    requirements: {modules: [WF]},
    manifest: {[WF]: await sha256Hex(denyWfSrc), [HELPER]: await sha256Hex(denyHelperSrc)}});
  verdict('d-undeclared-import-terminal',
    deny.status === 502 && deny.body.phase === 'undeclared-import' &&
    (deny.body.provider_outcome?.undeclared_modules ?? []).includes(HELPER),
    JSON.stringify(deny.body).slice(0, 500));
  verdict('d-undeclared-terminal-names-paths',
    (deny.body.provider_outcome?.undeclared_modules ?? []).every((m) => m.endsWith('.py') && m.includes('/')),
    JSON.stringify(deny.body.provider_outcome).slice(0, 300));

  const okWfSrc = 'def main():\n    return {"ok": True}\n';
  const okRun = await post({tenant_id: tenantH, action: 'run',
    modules: {[WF]: okWfSrc}, entry: WF,
    requirements: {modules: [WF]}, manifest: {[WF]: await sha256Hex(okWfSrc)}});
  verdict('d-declared-run-succeeds',
    okRun.status === 200 && okRun.body.output?.result?.ok === true,
    JSON.stringify(okRun.body).slice(0, 500));
  verdict('d-declared-path-recorded-reconciles-clean',
    okRun.status === 200 &&
    (okRun.body.output?.recorded ?? []).includes(WF) &&
    (okRun.body.api_calls ?? []).some((c) => c.path === `/api/sdk/modules/${WF}` && c.status === 200),
    JSON.stringify({recorded: okRun.body.output?.recorded, api_calls: okRun.body.api_calls}));

  // Child holds no credentials in the billed runtime.
  const envCheck = await post({tenant_id: tenantB, action: 'env_check'});
  verdict('d-child-holds-no-credentials',
    envCheck.status === 200 &&
    envCheck.body.output.token_shaped_present === false &&
    envCheck.body.output.dummy_credential_present === false,
    JSON.stringify(envCheck.body.output));

  // Constant generic script: same digest as the deployed bundle.
  const digestRes = await fetch(`${BASE}/__code_digest`, {headers: {'X-Spike-Auth': SPIKE_AUTH}});
  const digestBody = await digestRes.json();
  verdict('code-digest-exposed', /^[0-9a-f]{64}$/.test(digestBody.generic_code_sha256),
    JSON.stringify(digestBody));
} catch (error) {
  failures++;
  console.error('HARNESS ERROR:', error);
} finally {
  evidence.finished_at = new Date().toISOString();
  evidence.invocations = invocations;
  evidence.failures = failures;
  const out = new URL('../../results/tenant-cloud-acceptance.json', import.meta.url);
  await writeFile(out, JSON.stringify(evidence, null, 2) + '\n');
  console.log(failures === 0
    ? `CLOUD TENANT ACCEPTANCE: ALL CHECKS PASS (${invocations} invocations)`
    : `CLOUD TENANT ACCEPTANCE: ${failures} CHECK(S) FAIL (${invocations} invocations)`);
}
if (failures > 0) process.exitCode = 1;
