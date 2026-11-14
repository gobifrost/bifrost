// CI gate for the workspace eligibility census.
//
// Runs the same rule the dispatcher uses and fails the build when the routing
// split regresses: either the overall eligibility ratio drops below a threshold,
// or a workflow that was eligible in the recorded baseline is no longer.
//
//   node census-gate.mjs --workspace <root> --allowance 50 --baseline baseline.json
//
// Exit 1 on regression. No dependencies beyond Node.
import {spawnSync} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const args = process.argv.slice(2);
const flag = (name, fallback) => args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback;
const workspace = flag('workspace', null);
if (!workspace) {
  console.error('census-gate: --workspace <root> is required');
  process.exit(2);
}
const allowance = Number(flag('allowance', '50'));
const threshold = Number(flag('threshold', '0.95'));
const baselinePath = flag('baseline', null);
const outPath = flag('out', join(tmpdir(), `census-gate-${Date.now()}.json`));

const census = fileURLToPath(new URL('./census.mjs', import.meta.url));
const run = spawnSync(process.execPath, [census, '--workspace', workspace,
  '--allowance', String(allowance), '--out', outPath], {encoding: 'utf8'});
if (run.status !== 0) {
  console.error(run.stdout ?? '');
  console.error(run.stderr ?? '');
  console.error(`census-gate: census failed with status ${run.status}`);
  process.exit(2);
}

const report = JSON.parse(await readFile(outPath, 'utf8'));
const rows = [];
for (const ws of report.workspaces ?? []) {
  for (const row of ws.rows ?? []) rows.push({key: `${ws.root}::${row.path}::${row.workflow ?? ''}`, ...row});
}
const eligible = rows.filter((r) => r.backend === 'cloudflare').length;
const ratio = rows.length ? eligible / rows.length : 1;
console.log(`census-gate: ${eligible}/${rows.length} eligible (${(100 * ratio).toFixed(1)}%) at allowance ${allowance}`);
console.log(`census-gate: threshold ${(100 * threshold).toFixed(1)}%`);

let failed = false;
for (const row of rows.filter((r) => r.backend !== 'cloudflare')) {
  console.log(`  linux: ${row.key} — ${row.reasons?.[0] ?? 'no reason recorded'}`);
}

if (ratio < threshold) {
  console.error(`census-gate: FAIL — eligibility ${(100 * ratio).toFixed(1)}% is below the ${(100 * threshold).toFixed(1)}% threshold`);
  failed = true;
}

if (baselinePath) {
  const baseline = JSON.parse(await readFile(baselinePath, 'utf8'));
  const wasEligible = new Set();
  for (const ws of baseline.workspaces ?? []) {
    for (const row of ws.rows ?? []) {
      if (row.backend === 'cloudflare') wasEligible.add(`${ws.root}::${row.path}::${row.workflow ?? ''}`);
    }
  }
  const regressions = [...wasEligible].filter((key) => !rows.some((r) => r.key === key && r.backend === 'cloudflare'));
  if (regressions.length) {
    console.error(`census-gate: FAIL — ${regressions.length} workflow(s) regressed from eligible to Linux:`);
    for (const key of regressions.slice(0, 25)) console.error(`  ${key}`);
    failed = true;
  } else {
    console.log('census-gate: no baseline regressions');
  }
}

process.exit(failed ? 1 : 0);
