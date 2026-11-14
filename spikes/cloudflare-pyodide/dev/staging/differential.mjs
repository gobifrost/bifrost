// Stage 2 differential replay: run the same workflow on the Linux worker and on
// the Cloudflare per-tenant supervisor, then compare the observable result.
//
//   node differential.mjs --api http://127.0.0.1:18000 \
//     --worker https://bifrost-pyodide-tenant-<id>.<account>.workers.dev
//
// Secrets are read from files, never argv: STAGE_TOKEN_FILE, WORKER_SECRET_FILE.
// Static comparison: status class and result JSON. Logs/effects are the next
// increment (they need the execution log endpoint, not just the result).
import {readFile, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const args = process.argv.slice(2);
const flag = (name, fallback) => args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback;
const API = flag('api', 'http://127.0.0.1:18000');
const WORKER = flag('worker', null);
const tokenFile = flag('token-file', join(tmpdir(), 'pve-token.txt'));
const secretFile = flag('secret-file', join(tmpdir(), 'stage-secret.txt'));
if (!WORKER) throw new Error('--worker is required');

const token = (await readFile(tokenFile, 'utf8')).trim();
const secret = (await readFile(secretFile, 'utf8')).trim();
const auth = {Authorization: `Bearer ${token}`};
let meCache = null;

async function api(path, init = {}) {
  const response = await fetch(`${API}${path}`, {...init, headers: {...auth, ...(init.headers ?? {})}});
  const text = await response.text();
  let body;
  try { body = JSON.parse(text); } catch { body = {raw: text}; }
  return {status: response.status, body};
}

// Cases run on both backends. `files` are written to the workspace first so the
// API serves real pinned source; `entry` is the storage path.
const CASES = [
  {
    name: 'imports-helper',
    entry: 'workflows/case_import.py',
    files: {
      'workflows/case_import.py': 'from workflows.case_helper import double\n\ndef main():\n    return {"ok": True, "doubled": double(21)}\n',
      'workflows/case_helper.py': 'def double(value):\n    return value * 2\n',
    },
    required: ['workflows/case_import.py', 'workflows/case_helper.py'],
  },
  {
    name: 'stdlib-only',
    entry: 'workflows/case_math.py',
    files: {'workflows/case_math.py': 'def main():\n    return {"sum": sum(range(11)), "sorted": sorted([3, 1, 2])}\n'},
    required: ['workflows/case_math.py'],
  },
  {
    name: 'raises',
    entry: 'workflows/case_raise.py',
    files: {'workflows/case_raise.py': 'def main():\n    raise ValueError("expected diagnostic")\n'},
    required: ['workflows/case_raise.py'],
    expectFailure: true,
  },
  {
    // SDK case: runs the real engine on Cloudflare and a registered workflow on
    // Linux. Read-only by design (tables.list) so both backends see the same state.
    name: 'sdk-tables-list',
    sdk: true,
    entry: 'workflows/case_sdk.py',
    function: 'main',
    files: {
      'workflows/case_sdk.py': 'from bifrost import tables, workflow\n\n@workflow(name="__FN__")\nasync def __FN__():\n    rows = await tables.list()\n    return {"count": len(rows), "kind": type(rows).__name__}\n',
    },
    required: ['workflows/case_sdk.py'],
  },
  {
    // Log case: the engine captures workflow logs on both backends. The marker
    // must appear in both, which proves the log path, not just the result.
    name: 'logs-marker',
    sdk: true,
    logs: true,
    entry: 'workflows/case_logs.py',
    function: 'main',
    files: {
      'workflows/case_logs.py': 'import logging\nfrom bifrost import workflow\n\n@workflow(name="__FN__")\nasync def __FN__():\n    logging.getLogger("workflow").info("differential-log-marker")\n    return {"ok": True}\n',
    },
    required: ['workflows/case_logs.py'],
  },
];

// Key order is not meaningful across the two backends.
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

async function writeCase(c) {
  for (const [path, content] of Object.entries(c.files)) {
    const r = await api('/api/files/write', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path, content, location: 'workspace'})});
    if (r.status !== 200 && r.status !== 204) throw new Error(`write ${path} failed: ${r.status}`);
  }
  const manifest = {};
  for (const path of c.required) {
    const r = await api(`/api/sdk/modules/${path}`);
    if (r.status !== 200) throw new Error(`pin ${path} failed: ${r.status}`);
    manifest[path] = r.body.hash;
  }
  return manifest;
}

async function runCloudflare(c, manifest, runId) {
  const body = {tenant_id: `diff-${c.name}-${runId}`, action: 'run', entry: c.entry,
    requirements: {modules: c.required}, manifest, context: {engine_token: token},
    egress: {hosts: []}};
  const response = await fetch(WORKER, {method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Spike-Auth': secret},
    body: JSON.stringify(body), signal: AbortSignal.timeout(180000)});
  const json = await response.json();
  return {httpStatus: response.status, phase: json.phase ?? null,
    result: json.output?.result ?? null, error: json.phase ?? null};
}

async function me() {
  if (!meCache) {
    const r = await api('/auth/me');
    meCache = r.body;
  }
  return meCache;
}

async function runCloudflareEngine(c, manifest, runId) {
  const user = await me();
  const context = {
    caller: {user_id: user.id, email: user.email, name: 'Differential'},
    organization_id: user.organization_id,
    name: c.name.replaceAll('-', '_'),
    function_name: c.function,
    file_path: c.entry,
    engine_token: token,
    transient: true,
  };
  const body = {tenant_id: `diff-eng-${c.name}-${runId}`, action: 'engine',
    execution_id: crypto.randomUUID(), requirements: {modules: c.required},
    manifest, context, public_url: 'https://staging.invalid', egress: {hosts: []}};
  const response = await fetch(WORKER, {method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Spike-Auth': secret},
    body: JSON.stringify(body), signal: AbortSignal.timeout(180000)});
  const json = await response.json();
  const out = json.output ?? null;
  return {httpStatus: response.status, phase: json.phase ?? null,
    status: out?.status ?? null, result: out?.result ?? null,
    error: out?.error_type ?? null, logs: out?.logs ?? []};
}

async function runLinuxRegistered(c) {
  const registered = await api('/api/workflows/register', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: c.entry, function_name: c.function})});
  let id = registered.body?.workflow_id ?? registered.body?.id ?? null;
  if (!id) {
    // Already registered (409) or the response omitted the id: resolve by path
    // and function from the workflow list.
    const list = await api('/api/workflows');
    const match = (list.body ?? []).find((w) =>
      (w.relative_file_path === c.entry || w.source_file_path === c.entry) &&
      w.function_name === c.function);
    if (!match) {
      return {httpStatus: registered.status, status: `RegisterFailed(${registered.status})`, result: null};
    }
    id = match.id;
  }
  const run = await api('/api/workflows/execute', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({workflow_id: id, sync: false, transient: true})});
  if (run.status !== 200) return {httpStatus: run.status, status: 'SubmitFailed', result: null};
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const r = await api(`/api/executions/${run.body.execution_id}`);
    if (r.body.status && !['Pending', 'Running'].includes(r.body.status)) {
      const logs = await api(`/api/executions/${run.body.execution_id}/logs`);
      return {httpStatus: r.status, status: r.body.status, result: r.body.result ?? null,
        error: r.body.error ?? null, logs: logs.body ?? null};
    }
  }
  return {httpStatus: 200, status: 'Timeout', result: null};
}

async function runLinux(c) {
  const code = `${c.files[c.entry]}\nresult = main()\n`;
  const submitted = await api('/api/workflows/execute', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({code, script_name: `diff-${c.name}`, sync: false, transient: true})});
  if (submitted.status !== 200) return {httpStatus: submitted.status, status: 'SubmitFailed', result: null};
  const id = submitted.body.execution_id;
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const r = await api(`/api/executions/${id}`);
    if (r.body.status && !['Pending', 'Running'].includes(r.body.status)) {
      return {httpStatus: r.status, status: r.body.status, result: r.body.result ?? null,
        error: r.body.error ?? null};
    }
  }
  return {httpStatus: 200, status: 'Timeout', result: null};
}

// A per-run suffix keeps SDK registration conflict-free: stale inactive rows for
// a previous run must not block a fresh registration.
function withRunId(c, runId) {
  if (!c.sdk) return c;
  const remap = (p) => p.replace(/\.py$/, `_${runId}.py`);
  const fn = `case_${c.name.replaceAll('-', '_')}_${runId}`;
  return {...c, entry: remap(c.entry), function: c.sdk ? fn : c.function,
    files: Object.fromEntries(Object.entries(c.files).map(([p, s]) => [remap(p), s.replaceAll('__RUN_ID__', runId).replaceAll('__FN__', fn)])),
    required: c.required.map(remap)};
}

const runId = Date.now().toString(36);
const evidence = {started_at: new Date().toISOString(), run_id: runId, cases: []};
let failures = 0;

for (const rawCase of CASES) {
  const c = withRunId(rawCase, runId);
  const manifest = await writeCase(c);
  const cloud = c.sdk
    ? await runCloudflareEngine(c, manifest, runId)
    : await runCloudflare(c, manifest, runId);
  const linux = c.sdk ? await runLinuxRegistered(c) : await runLinux(c);

  const sameResult = canonical(cloud.result) === canonical(linux.result);
  const cloudFailed = c.sdk ? cloud.status !== 'Success' : (cloud.httpStatus >= 400 || cloud.phase !== null);
  const linuxFailed = linux.status !== 'Success';
  const sameStatus = cloudFailed === linuxFailed;
  const marker = 'differential-log-marker';
  const sameLogs = !c.logs ||
    (JSON.stringify(cloud.logs ?? []).includes(marker) && JSON.stringify(linux.logs ?? []).includes(marker));
  const ok = c.expectFailure ? sameStatus : (sameResult && sameStatus && sameLogs);
  if (!ok) failures++;
  evidence.cases.push({name: c.name, ok, cloud, linux, same_result: sameResult,
    same_status: sameStatus, same_logs: sameLogs});
  console.log(`${ok ? 'PASS' : 'FAIL'} ${c.name} — cloud=${c.sdk ? cloud.status : `${cloud.httpStatus}/${cloud.phase ?? 'ok'}`} ` +
    `linux=${linux.status} same_result=${sameResult} same_status=${sameStatus} same_logs=${sameLogs}`);
  if (!ok) console.log(`  cloud: ${JSON.stringify(cloud.result)} linux: ${JSON.stringify(linux.result)}`);
}

evidence.finished_at = new Date().toISOString();
evidence.failures = failures;
await writeFile(new URL('../../results/differential-replay.json', import.meta.url),
  JSON.stringify(evidence, null, 2) + '\n');
console.log(failures === 0 ? 'DIFFERENTIAL: ALL CASES MATCH' : `DIFFERENTIAL: ${failures} CASE(S) DIFFER`);
process.exitCode = failures === 0 ? 0 : 1;
