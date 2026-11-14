// Read back the Dynamic Worker creation meter.
//
// This is the ADR's outstanding acceptance item: confirm in the billed
// environment that a per-tenant id scheme is counted per (worker id, code), not
// per invocation. Read-only; the OAuth token is read into memory and never
// printed or written.
import {readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {homedir} from 'node:os';

const ACCOUNT = 'b5fc61a1bec847ecc83b3b880b563aaf';
const configPath = join(homedir(), 'AppData', 'Roaming', 'xdg.config', 'cloudflare', 'config', 'default.json');
const config = JSON.parse((await readFile(configPath, 'utf8')).replace(/^\uFEFF/, ''));
const token = config.oauth_token;
if (typeof token !== 'string' || token.length < 20) throw new Error('No OAuth token available');

const date = process.argv[2] ?? new Date().toISOString().slice(0, 10);
const query = `query getDynamicWorkersCount($accountTag: string!, $filter: AccountWorkersInvocationsByOwnerAndScriptGroupsFilter_InputObject) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      workersInvocationsByOwnerAndScriptGroups(limit: 10000, filter: $filter) {
        uniq { distinctDynamicWorkerCount }
        sum { requests }
      }
    }
  }
}`;

const response = await fetch('https://api.cloudflare.com/client/v4/graphql', {
  method: 'POST',
  headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
  body: JSON.stringify({query, variables: {accountTag: ACCOUNT,
    filter: {date_geq: date, date_leq: date}}}),
});
const text = await response.text();
let parsed;
try { parsed = JSON.parse(text); } catch { parsed = {unparsed: text.slice(0, 2000)}; }
if (!response.ok || parsed.errors) {
  console.error(JSON.stringify(parsed, null, 2).slice(0, 4000));
  process.exitCode = 1;
} else {
  const rows = parsed.data?.viewer?.accounts?.[0]?.workersInvocationsByOwnerAndScriptGroups ?? [];
  const uniq = rows.reduce((n, r) => n + (r.uniq?.distinctDynamicWorkerCount ?? 0), 0);
  const requests = rows.reduce((n, r) => n + (r.sum?.requests ?? 0), 0);
  const out = {account: ACCOUNT, date, distinct_dynamic_workers: uniq,
    requests: requests, rows: rows.length,
    queried_at: new Date().toISOString()};
  console.log(JSON.stringify(out, null, 2));
  await writeFile(new URL('../../results/tenant-creation-meter.json', import.meta.url),
    JSON.stringify(out, null, 2) + '\n');
}
