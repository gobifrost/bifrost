// Hostile Phase-1 driver: honest baseline + hostile variant, sequential AND
// concurrent, fresh isolate per attempt. Fails closed: any leak is RED.
// Usage: node backend/hostile-isolation.test.mjs [--url http://127.0.0.1:8796] [--out /tmp/...json]
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';

const args = process.argv.slice(2);
const urlIdx = args.indexOf('--url');
const outIdx = args.indexOf('--out');
const BASE = urlIdx >= 0 ? args[urlIdx + 1] : 'http://127.0.0.1:8796';
const OUT = outIdx >= 0 ? args[outIdx + 1] : null;
const SECRET_A = `hostile-cred-${Date.now().toString(36)}-a`;

const results = [];
const canaries = [];

async function probe(body, auth = process.env.SPIKE_AUTH ?? null) {
  const headers = {'Content-Type': 'application/json'};
  if (auth) headers['X-Spike-Auth'] = auth;
  const response = await fetch(BASE, {method: 'POST', headers,
    body: JSON.stringify(body), signal: AbortSignal.timeout(60000)});
  const text = await response.text();
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = {unparsed: text.slice(0, 4000)}; }
  const record = {request: body, status: response.status, body: parsed};
  results.push(record);
  return record;
}

function verdict(name, pass, evidence, detail = '') {
  canaries.push({name, pass, evidence, detail});
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
  return pass;
}

const auth = process.env.SPIKE_AUTH ?? null;
let failures = 0;
const need = (name, cond, evidence, detail) => {
  if (!verdict(name, !!cond, evidence, detail)) failures++;
};

try {
  // 0. Supervisor gate: unauthenticated must fail closed when auth is configured (cloud).
  // Local LOCAL_PROBE=true allows no-auth; record only.
  const honest0 = await probe({action: 'honest', marker: 'baseline-0'});
  assert.equal(honest0.status, 200, 'supervisor must accept honest probe');
  assert.equal(honest0.body.revision, 1);

  // 1. builtins mutation
  const bPlant = await probe({action: 'plant_builtins', marker: 'builtins-A'});
  assert.equal(bPlant.body.outcome.status, 200);
  const bRead = await probe({action: 'read_builtins', marker: 'builtins-B'});
  need('1-builtins-sequential', bRead.body.outcome.body.builtins == null && bRead.body.outcome.body.stdlib_json == null,
    bRead, JSON.stringify(bRead.body.outcome.body));
  const [bR1, bR2] = await Promise.all([
    probe({action: 'plant_builtins', marker: 'builtins-conc-A'}),
    probe({action: 'read_builtins', marker: 'builtins-conc-B'}),
  ]);
  // Concurrent reader must be clean regardless of overlap order; planter plants only its own isolate.
  need('1-builtins-concurrent', bR2.body.outcome.body.builtins == null && bR2.body.outcome.body.stdlib_json == null,
    {plant: bR1, read: bR2}, JSON.stringify(bR2.body.outcome.body));

  // 2. module state
  await probe({action: 'plant_module', marker: 'module-A'});
  const mRead = await probe({action: 'read_module', marker: 'module-B'});
  need('2-module-sequential', mRead.body.outcome.body.present === false && mRead.body.outcome.body.marker == null,
    mRead, JSON.stringify(mRead.body.outcome.body));
  const [mP, mR] = await Promise.all([
    probe({action: 'plant_module', marker: 'module-conc-A'}),
    probe({action: 'read_module', marker: 'module-conc-B'}),
  ]);
  need('2-module-concurrent', mR.body.outcome.body.present === false,
    {plant: mP, read: mR}, JSON.stringify(mR.body.outcome.body));

  // 3. environment
  await probe({action: 'plant_env', marker: 'env-A'});
  const eRead = await probe({action: 'read_env', marker: 'env-B'});
  need('3-env-sequential', eRead.body.outcome.body.value == null, eRead, JSON.stringify(eRead.body.outcome.body));
  const [eP, eR] = await Promise.all([
    probe({action: 'plant_env', marker: 'env-conc-A'}),
    probe({action: 'read_env', marker: 'env-conc-B'}),
  ]);
  need('3-env-concurrent', eR.body.outcome.body.value == null, {plant: eP, read: eR}, JSON.stringify(eR.body.outcome.body));

  // 4. filesystem
  await probe({action: 'plant_fs', marker: 'fs-A'});
  const fRead = await probe({action: 'read_fs', marker: 'fs-B'});
  need('4-fs-sequential', fRead.body.outcome.body.exists === false && fRead.body.outcome.body.value == null,
    fRead, JSON.stringify(fRead.body.outcome.body));
  const [fP, fR] = await Promise.all([
    probe({action: 'plant_fs', marker: 'fs-conc-A'}),
    probe({action: 'read_fs', marker: 'fs-conc-B'}),
  ]);
  need('4-fs-concurrent', fR.body.outcome.body.exists === false,
    {plant: fP, read: fR}, JSON.stringify(fR.body.outcome.body));

  // 5. credential separation (strongest): A gets secret, B (no secret) hunts it.
  const cPlant = await probe({action: 'plant_creds', marker: 'creds-A', secret: SECRET_A});
  assert.equal(cPlant.body.outcome.status, 200);
  const cRead = await probe({action: 'read_creds', marker: 'creds-B'});
  const cb = cRead.body.outcome.body;
  const credClean = cb.builtins_present === false && cb.env_present === false &&
    cb.module_present === false && cb.fs_present === false && cb.global_present === false &&
    cb.canary_secret_env_present === false;
  need('5-creds-separation', credClean, cRead, JSON.stringify(cb));
  // SDK client must not yield credentials: importable is ok only if it carries no secret.
  // Fail closed: any present flag above already fails; record sdk_import only.
  console.log(`  sdk_import observed: ${cb.sdk_import}`);

  // 6. concurrent tenants: two hostile planters overlapping, then two clean readers.
  const [cA, cB] = await Promise.all([
    probe({action: 'plant_concurrent', marker: 'tenant-A-marker'}),
    probe({action: 'plant_concurrent', marker: 'tenant-B-marker'}),
  ]);
  const [rA, rB] = await Promise.all([
    probe({action: 'read_concurrent', marker: 'reader-A'}),
    probe({action: 'read_concurrent', marker: 'reader-B'}),
  ]);
  const cleanReader = (r) => {
    const b = r.body.outcome.body;
    return b.builtins == null && b.env == null && b.module == null && b.fs == null && b.stdlib_json == null;
  };
  need('6-concurrent-A-clean', cleanReader(rA), {planters: [cA, cB], reader: rA}, JSON.stringify(rA.body.outcome.body));
  need('6-concurrent-B-clean', cleanReader(rB), {planters: [cA, cB], reader: rB}, JSON.stringify(rB.body.outcome.body));

  // 7. user-authored import: helper plants via virtual import, fresh reader must be clean.
  const iPlant = await probe({action: 'import_plant', marker: 'import-A'});
  assert.equal(iPlant.body.outcome.status, 200);
  const iRead = await probe({action: 'import_read', marker: 'import-B'});
  const obs = iRead.body.outcome.body.observed;
  const importClean = obs.builtins == null && obs.env == null && obs.module == null &&
    obs.module_present === false && obs.fs == null;
  need('7-import-separation', importClean, iRead, JSON.stringify(obs));
  const [iP2, iR2] = await Promise.all([
    probe({action: 'import_plant', marker: 'import-conc-A'}),
    probe({action: 'import_read', marker: 'import-conc-B'}),
  ]);
  const obs2 = iR2.body.outcome.body.observed;
  need('7-import-concurrent', obs2.builtins == null && obs2.env == null && obs2.fs == null && obs2.module_present === false,
    {plant: iP2, read: iR2}, JSON.stringify(obs2));

  console.log(failures === 0 ? 'HOSTILE ISOLATION: ALL CANARIES PASS' : `HOSTILE ISOLATION: ${failures} CANARY CHECK(S) FAIL (RED)`);
} catch (error) {
  failures++;
  canaries.push({name: 'harness-error', pass: false, evidence: String(error?.stack ?? error)});
  console.error('HARNESS ERROR:', error);
} finally {
  // Never persist the plant secret value. Redact defensively before any write.
  const redacted = JSON.parse(JSON.stringify({runtime: 'hostile-fresh-isolate', base: BASE, canaries, results}).split(SECRET_A).join('[REDACTED-CRED]'));
  if (OUT) await writeFile(OUT, JSON.stringify(redacted, null, 2) + '\n');
  if (failures > 0) process.exitCode = 1;
}
