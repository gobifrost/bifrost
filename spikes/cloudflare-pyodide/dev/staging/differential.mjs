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
];

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

const runId = Date.now().toString(36);
const evidence = {started_at: new Date().toISOString(), run_id: runId, cases: []};
let failures = 0;

for (const c of CASES) {
  const manifest = await writeCase(c);
  const cloud = await runCloudflare(c, manifest, runId);
  const linux = await runLinux(c);

  const sameResult = JSON.stringify(cloud.result) === JSON.stringify(linux.result);
  const cloudFailed = cloud.httpStatus >= 400 || cloud.phase !== null;
  const linuxFailed = linux.status !== 'Success';
  const sameStatus = cloudFailed === linuxFailed;
  const ok = c.expectFailure ? sameStatus : (sameResult && sameStatus);
  if (!ok) failures++;
  evidence.cases.push({name: c.name, ok, cloud, linux, same_result: sameResult, same_status: sameStatus});
  console.log(`${ok ? 'PASS' : 'FAIL'} ${c.name} — cloud=${cloud.httpStatus}/${cloud.phase ?? 'ok'} ` +
    `linux=${linux.status} same_result=${sameResult} same_status=${sameStatus}`);
  if (!ok) console.log(`  cloud: ${JSON.stringify(cloud.result)} linux: ${JSON.stringify(linux.result)}`);
}

evidence.finished_at = new Date().toISOString();
evidence.failures = failures;
await writeFile(new URL('../../results/differential-replay.json', import.meta.url),
  JSON.stringify(evidence, null, 2) + '\n');
console.log(failures === 0 ? 'DIFFERENTIAL: ALL CASES MATCH' : `DIFFERENTIAL: ${failures} CASE(S) DIFFER`);
process.exitCode = failures === 0 ? 0 : 1;
