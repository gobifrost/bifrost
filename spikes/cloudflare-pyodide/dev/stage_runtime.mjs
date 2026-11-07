import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {stageRuntimeLoader} from './stage-runtime-loader.mjs';
const path = process.argv[2];
if (!path) throw new Error('Supply the public prepared generated-runtime.mjs artifact');
const bytes = await readFile(path);
const digest = createHash('sha256').update(bytes).digest('hex');
if (digest !== 'e1cca587e10cb755d49df0f2325c038a361a25f7031142a9e9e896a3c004bd39') {
  throw new Error('Prepared runtime artifact integrity mismatch');
}
const directory = new URL('./runtime/', import.meta.url);
await mkdir(directory, {recursive: true});
const prefix = 'export default ';
const text = bytes.toString('utf8');
if (!text.startsWith(prefix) || !text.endsWith(';\n')) throw new Error('Unexpected prepared artifact format');
const modules = JSON.parse(text.slice(prefix.length, -2));
const bootstrap = (await readFile(new URL('../backend/runtime-runner.py', import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
modules['runtime_runner.py'] = {py: bootstrap};
const enginePath = 'src/services/execution/engine.py';
const sha256 = value => createHash('sha256').update(value).digest('hex');
if (sha256(modules[enginePath].py) !== 'b9bd1d04a17d9dfc6a9418550ffb361c734d99558bdd7c710f3c53b0fdbc06ab') {
  throw new Error('Unexpected base engine; reconcile its upstream provenance');
}
const engine = (await readFile(new URL('../../../api/src/services/execution/engine.py', import.meta.url), 'utf8')).replaceAll('\r\n', '\n');
if (sha256(engine) !== '6690efa6fc513a0f457c30cfed59da091bf3ae292d186bc72cd436c871759c21') {
  throw new Error('Engine differs from the native/Pyodide-qualified stream-capture revision');
}
modules[enginePath] = {py: engine};
const output = Buffer.from(prefix + JSON.stringify(modules) + ';\n');
await writeFile(new URL('generated-runtime.mjs', directory), output);
const loaderFiles = await stageRuntimeLoader(directory);
const provenance = {base_runtime_sha256: digest,
  loader_files_sha256: loaderFiles,
  runtime_sha256: createHash('sha256').update(output).digest('hex'),
  bootstrap_sha256: createHash('sha256').update(bootstrap).digest('hex'),
  engine_sha256: sha256(engine),
  engine_source: 'f49d4ba99cebd24aa497f1244833b940e640885f',
  upstream_source: '785b560b9973020e1c13401383e1a73a87a54940',
  note: 'Companion bootstrap and qualified upstream engine stream-capture revision replaced; all other engine/SDK modules and native packages retain the pinned base closure'};
await writeFile(new URL('runtime-provenance.json', directory), JSON.stringify(provenance, null, 2) + '\n');
console.log(JSON.stringify(provenance));
