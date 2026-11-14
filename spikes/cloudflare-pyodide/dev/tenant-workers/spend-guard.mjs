// Spend guard for the Cloudflare backend.
//
// Wraps `cf billing spend-policies` (one policy per product per account) and
// prints the cost position the policy is supposed to protect. It is a thin
// operational wrapper, not a second control plane: Cloudflare owns enforcement.
//
// Critical scoping fact, from the Cloudflare docs and `--product-id` choices:
// a Workers spend policy covers **requests and CPU time** across the account.
// Dynamic Worker *creation* charges ($0.002 per worker per day) are a separate
// Dynamic Workers line and are NOT capped by it. Creation is the dominant cost
// in the per-attempt posture, so this guard cannot bound that spend; only the
// dispatcher can, by choosing per-tenant ids.
//
// Usage:
//   node spend-guard.mjs list
//   node spend-guard.mjs plan [--tenants 35] [--runs 500] [--days 30]
//   node spend-guard.mjs apply --action notification --threshold-cents 2500 \
//     --email ops@example.com [--name "..."] [--dry-run]
//   node spend-guard.mjs apply --action usage_block --threshold-cents 50000 \
//     --email ops@example.com --confirm-block
//   node spend-guard.mjs delete <POLICY_ID>
//
// `usage_block` is refused without --confirm-block: it is account-wide and can
// take production Workers down when the threshold is reached.
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';

// Published Workers Paid rates, verified against
// developers.cloudflare.com/workers/platform/pricing and
// developers.cloudflare.com/dynamic-workers/pricing.
const RATES = {
  requestsIncludedPerMonth: 10_000_000,
  requestsUsdPerMillion: 0.30,
  cpuMsIncludedPerMonth: 30_000_000,
  cpuMsUsdPerMillion: 0.02,
  dynamicWorkersIncludedPerMonth: 1_000,
  dynamicWorkerUsdPerDay: 0.002,
  // Single parent-side sample from the CPU telemetry probe; the only measured
  // per-run CPU figure and explicitly not fleet data.
  measuredCpuMsPerRun: 1757,
};

const args = process.argv.slice(2);
const command = args[0];
const flag = (name, fallback) => args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback;
const has = (name) => args.includes(`--${name}`);

function quoteArg(value) {
  const text = String(value);
  return /^[A-Za-z0-9_@%+=:,./-]+$/.test(text) ? text : `"${text.replaceAll('"', '\\"')}"`;
}

function cf(argv, {dryRun = false} = {}) {
  const full = [...argv];
  if (dryRun) full.push('--dry-run');
  // Run from outside the project: the CLI prefers a project-local install
  // (spikes/cloudflare-pyodide/node_modules/cf is older and has no
  // spend-policies command). cwd does not affect account selection.
  // Arguments are quoted because shell:true only concatenates them.
  const line = ['cf', ...full.map(quoteArg)].join(' ');
  const result = spawnSync(line,
    {encoding: 'utf8', shell: process.platform === 'win32', cwd: tmpdir()});
  if (result.status !== 0) {
    process.stderr.write(result.stdout ?? '');
    process.stderr.write(result.stderr ?? '');
    process.exit(result.status ?? 1);
  }
  return result.stdout ?? '';
}

function money(value) {
  return `$${value.toFixed(2)}`;
}

function plan() {
  const tenants = Number(flag('tenants', '35'));
  const runs = Number(flag('runs', '500'));
  const days = Number(flag('days', '30'));

  const requests = runs;
  const cpuMs = runs * RATES.measuredCpuMsPerRun;
  // Per-tenant posture: one creation per tenant per day (reuse is free).
  const creations = tenants * days;

  const requestCost = Math.max(0, requests - RATES.requestsIncludedPerMonth) / 1e6 * RATES.requestsUsdPerMillion;
  const cpuCost = Math.max(0, cpuMs - RATES.cpuMsIncludedPerMonth) / 1e6 * RATES.cpuMsUsdPerMillion;
  const creationCost = Math.max(0, creations - RATES.dynamicWorkersIncludedPerMonth) * RATES.dynamicWorkerUsdPerDay;

  const capped = requestCost + cpuCost;
  const uncapped = creationCost;
  console.log(`Assumptions: ${tenants} tenants, ${runs} runs/month, ${days} days, ` +
    `${RATES.measuredCpuMsPerRun} CPU-ms/run (single measured sample).`);
  console.log('');
  console.log(`  requests   ${requests.toLocaleString()}            ${money(requestCost)}   (cap-able)`);
  console.log(`  CPU        ${(cpuMs / 1e6).toFixed(2)}M CPU-ms      ${money(cpuCost)}   (cap-able)`);
  console.log(`  creations  ${creations.toLocaleString()} workers-day   ${money(uncapped)}   (NOT cap-able)`);
  console.log('');
  console.log(`  Workers spend policy covers: ${money(capped)}`);
  console.log(`  Outside the policy:          ${money(uncapped)}  (Dynamic Workers creation)`);
  console.log(`  Modeled total:               ${money(capped + uncapped)}`);
  console.log('');
  const suggested = Math.max(500, Math.ceil((capped * 2) / 5) * 500);
  console.log(`  Suggested notification threshold: ${money(suggested / 100)} (2x cap-able spend)`);
  console.log('  For the per-attempt posture, creation cost dominates and is not capped:');
  console.log(`    ${runs.toLocaleString()} runs x $0.002 = ${money(runs * RATES.dynamicWorkerUsdPerDay)} before the included 1,000.`);
}

function list() {
  const output = cf(['billing', 'spend-policies', 'list']);
  const policies = JSON.parse(output || '[]');
  if (!policies.length) {
    console.log('No spend policies on this account.');
    return;
  }
  for (const p of policies) {
    console.log(`${p.id}  ${p.product_id.padEnd(16)} ${p.action.padEnd(13)} ` +
      `${money((p.threshold_cents ?? 0) / 100).padStart(10)}  enabled=${p.enabled}  ${p.name ?? ''}`);
  }
}

function apply() {
  const action = flag('action', 'notification');
  if (!['notification', 'usage_block'].includes(action)) {
    console.error('apply: --action must be notification or usage_block');
    process.exit(2);
  }
  const threshold = Number(flag('threshold-cents', '0'));
  if (!Number.isInteger(threshold) || threshold <= 0) {
    console.error('apply: --threshold-cents must be a positive integer');
    process.exit(2);
  }
  if (action === 'usage_block' && !has('confirm-block')) {
    console.error('apply: refusing usage_block without --confirm-block.');
    console.error('A Workers spend policy is ACCOUNT-WIDE: at the threshold Cloudflare rejects');
    console.error('new invocations for every Worker in the account, including production ones');
    console.error('(HTTP 1103), until the limit is raised, disabled, deleted, or the billing');
    console.error('period rolls over. Cloudflare does not scope it to this spike.');
    process.exit(3);
  }
  const email = flag('email', null);
  if (action === 'notification' && !email) {
    console.error('apply: --email is required for a notification policy');
    process.exit(2);
  }

  const existing = (JSON.parse(cf(['billing', 'spend-policies', 'list']) || '[]'))
    .find((p) => p.product_id === 'workers' && p.action === action);

  const common = ['billing', 'spend-policies'];
  if (existing) {
    common.push('update', existing.id, '--if-match', String(existing.version ?? 0));
    console.log(`Updating existing Workers ${action} policy ${existing.id} (version ${existing.version}).`);
  } else {
    common.push('create');
  }
  const argv = [...common,
    '--name', flag('name', `Bifrost Workers ${action} (${money(threshold / 100)})`),
    '--product-id', 'workers',
    '--enabled', 'true',
    '--threshold-cents', String(threshold),
    '--action', action];
  if (email) argv.push('--notification-email', email);
  const output = cf(argv, {dryRun: has('dry-run')});
  console.log(output.trim());
}

try {
  if (command === 'list') list();
  else if (command === 'plan') plan();
  else if (command === 'apply') apply();
  else if (command === 'delete') {
    const id = args[1];
    if (!id) throw new Error('delete requires a policy id');
    const current = JSON.parse(cf(['billing', 'spend-policies', 'get', id]));
    console.log(cf(['billing', 'spend-policies', 'delete', id, '--if-match', String(current.version ?? 0)]).trim());
  } else {
    console.error('usage: spend-guard.mjs list|plan|apply|delete');
    process.exit(2);
  }
} catch (error) {
  console.error(`spend-guard: ${error.message}`);
  process.exit(1);
}
