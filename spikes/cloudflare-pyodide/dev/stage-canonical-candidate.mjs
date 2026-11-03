// Offline assembly only. Existing cloud configuration/credentials are never changed.
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {stageQualifiedRuntime} from './stage-qualified-runtime.mjs';
const root = new URL('../', import.meta.url);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const files = ['probe-io.mjs',
  ...['cooperative-runtime.mjs', 'cooperative-rpc-result.mjs', 'lossless-json.mjs', 'fixture-admission.mjs',
    'attempt-egress.mjs', 'invocation-stream.mjs', 'terminal-response.mjs', 'module-response.mjs',
    'resolution-response.mjs', 'source-integrity-response.mjs', 'log-delivery-evidence.mjs'].map(name => 'backend/'+name),
  ...['canonical-entry.mjs', 'canonical-guard.mjs', 'policy.mjs', 'package.json', 'package-lock.json'].map(name => 'dev/executor/'+name)];

export async function stageCanonicalCandidate(input, output) {
  const bundle = await readFile(input);
  if (hash(bundle) !== '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731') {
    throw new Error('Exact qualified runtime required');
  }
  await mkdir(output);
  const manifest = {scope: 'offline guarded canonical SDK-only candidate; no deployment/runtime acceptance',
    defaults: {workers_dev: false, expiry: '0', credentials: 'none'}, files_sha256: {}};
  for (const name of files) {
    const bytes = await readFile(new URL(name, root));
    const target = resolve(output, name);
    await mkdir(dirname(target), {recursive: true});
    await writeFile(target, bytes, {flag: 'wx'});
    manifest.files_sha256[name] = hash(bytes);
  }
  await stageQualifiedRuntime(input, resolve(output, 'dev/runtime'));
  for (const [source, target] of [['cooperative_execution.py', 'cooperative-code.mjs'], ['rpc_serialization.py', 'rpc-serialization-code.mjs']]) {
    const bytes = await readFile(new URL('backend/'+source, root), 'utf8');
    const generated = `export default ${JSON.stringify(bytes)};`;
    await writeFile(resolve(output, 'dev/executor', target), generated, {flag: 'wx'});
    manifest.files_sha256['dev/executor/'+target] = hash(generated);
  }
  const original = await readFile(new URL('dev/executor/cloudflare.config.ts', root), 'utf8');
  if (original.split("entrypoint: './entry.mjs'").length !== 2) throw new Error('Existing guarded configuration changed');
  const config = original.replace("entrypoint: './entry.mjs'", "entrypoint: './canonical-entry.mjs'");
  await writeFile(resolve(output, 'dev/executor/cloudflare.config.ts'), config, {flag: 'wx'});
  manifest.files_sha256['dev/executor/cloudflare.config.ts'] = hash(config);
  await writeFile(resolve(output, 'candidate-provenance.json'), JSON.stringify(manifest, null, 2)+'\n', {flag: 'wx'});
  return manifest;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [input, output, ...extra] = process.argv.slice(2);
  if (!input || !output || extra.length) throw new Error('Supply exact bundle and a new task-owned output directory');
  console.log(JSON.stringify(await stageCanonicalCandidate(input, output)));
}
