// Controlled creation-meter delta experiment.
//
// Proves the billing rule the ADR depends on, in the billed account:
//   - stable (tenant id, code) reused across invocations => 0 new creations
//   - first use of a new (tenant id, code)             => 1 new creation
//   - `--mode load` (per-attempt posture control)      => 1 creation per invocation
//
// Analytics lags a couple of minutes, so every meter read waits first
// (--wait, default 150s). Read-only against the account meter; the OAuth token
// stays in memory.
import {readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {homedir, tmpdir} from 'node:os';

const ACCOUNT = 'b5fc61a1bec847ecc83b3b880b563aaf';
const BASE = 'https://bifrost-pyodide-tenant-ddf316c1.midtown-technology-group.workers.dev';
const configPath = join(homedir(), 'AppData', 'Roaming', 'xdg.config', 'cloudflare', 'config', 'default.json');
const config = JSON.parse((await readFile(configPath, 'utf8')).replace(/^\uFEFF/, ''));
const token = config.oauth_token;
if (typeof token !== 'string' || token.length < 20) throw new Error('No OAuth token available');
const {SPIKE_AUTH} = JSON.parse((await readFile(join(tmpdir(), 'bifrost-pyodide-tenant-ddf316c1-secrets.json'), 'utf8')).replace(/^\uFEFF/, ''));

const args = process.argv.slice(2);
const flag = (name, fallback) => args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback;
const mode = flag('mode', 'get');
const count = Number(flag('count', '3'));
const waitSeconds = Number(flag('wait', '150'));
const only = flag('only', 'all');
const tag = flag('tag', `meter-${Date.now().toString(36)}`);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function meter() {
  const query = `query getDynamicWorkersCount($accountTag: string!, $filter: AccountWorkersInvocationsByOwnerAndScriptGroupsFilter_InputObject) {
    viewer { accounts(filter: {accountTag: $accountTag}) {
      workersInvocationsByOwnerAndScriptGroups(limit: 10000, filter: $filter) {
        uniq { distinctDynamicWorkerCount } sum { requests }
      } } } }`;
  const date = new Date().toISOString().slice(0, 10);
  const response = await fetch('https://api.cloudflare.com/client/v4/graphql', {
    method: 'POST',
    headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
    body: JSON.stringify({query, variables: {accountTag: ACCOUNT, filter: {date_geq: date, date_leq: date}}}),
  });
  const parsed = await response.json();
  if (parsed.errors) throw new Error(JSON.stringify(parsed.errors));
  const rows = parsed.data?.viewer?.accounts?.[0]?.workersInvocationsByOwnerAndScriptGroups ?? [];
  return {
    workers: rows.reduce((n, r) => n + (r.uniq?.distinctDynamicWorkerCount ?? 0), 0),
    requests: rows.reduce((n, r) => n + (r.sum?.requests ?? 0), 0),
  };
}

async function probe(i) {
  const body = mode === 'load'
    ? {tenant_id: `${tag}-${i}`, action: 'legacy_load'}
    : {tenant_id: `${tag}-${i}`, action: 'honest'};
  const response = await fetch(BASE, {method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Spike-Auth': SPIKE_AUTH},
    body: JSON.stringify(body), signal: AbortSignal.timeout(180000)});
  const parsed = await response.json();
  if (response.status !== 200) throw new Error(`probe failed: ${JSON.stringify(parsed).slice(0, 400)}`);
  return {index: i, tenant: body.tenant_id, tenant_worker_id: parsed.tenant_worker_id ?? null,
    worker_reused: parsed.worker_reused ?? null};
}

const evidence = {mode, only, tag, count, wait_seconds: waitSeconds, started_at: new Date().toISOString(), steps: []};
const read = async (label) => {
  if (waitSeconds > 0) await sleep(waitSeconds * 1000);
  const m = await meter();
  console.log(`${label}: workers=${m.workers} requests=${m.requests}`);
  return m;
};

const before = await read('baseline');
let ok = false;

if (only === 'reuse') {
  // Stable ids only: invoking existing (id, code) pairs must create nothing.
  for (let i = 0; i < count; i++) evidence.steps.push({phase: 'reuse', ...(await probe(i))});
  const after = await read('after_reuse');
  evidence.before = before; evidence.after = after;
  evidence.reuse_delta = after.workers - before.workers;
  ok = evidence.reuse_delta === 0;
} else {
  for (let i = 0; i < count; i++) evidence.steps.push({phase: 'first-use', ...(await probe(i))});
  const afterFirst = await read('after_first_use');
  for (let i = 0; i < count; i++) evidence.steps.push({phase: mode === 'load' ? 'load-again' : 'reuse', ...(await probe(i))});
  const afterReuse = await read('after_second_round');
  evidence.before = before; evidence.after_first_use = afterFirst; evidence.after_second_round = afterReuse;
  evidence.first_use_delta = afterFirst.workers - before.workers;
  evidence.second_round_delta = afterReuse.workers - afterFirst.workers;
  ok = mode === 'load'
    ? evidence.first_use_delta === count && evidence.second_round_delta === count
    : evidence.first_use_delta === count && evidence.second_round_delta === 0;
}

evidence.ok = ok;
evidence.finished_at = new Date().toISOString();
await writeFile(new URL(`../../results/tenant-meter-${mode}-${only}-${tag}.json`, import.meta.url),
  JSON.stringify(evidence, null, 2) + '\n');
console.log(ok ? 'METER RULE: CONFIRMED' : 'METER RULE: NOT CONFIRMED');
if (!ok) process.exitCode = 1;
