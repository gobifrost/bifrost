import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
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
const output = Buffer.from(prefix + JSON.stringify(modules) + ';\n');
await writeFile(new URL('generated-runtime.mjs', directory), output);
await writeFile(new URL('runtime-code.mjs', directory), await readFile(new URL('../backend/runtime-code.mjs', import.meta.url)));
const provenance = {base_runtime_sha256: digest,
  runtime_sha256: createHash('sha256').update(output).digest('hex'),
  bootstrap_sha256: createHash('sha256').update(bootstrap).digest('hex'),
  upstream_source: '785b560b9973020e1c13401383e1a73a87a54940',
  note: 'Only companion runtime_runner.py replaced; upstream engine, SDK and native packages retain the pinned base closure'};
await writeFile(new URL('runtime-provenance.json', directory), JSON.stringify(provenance, null, 2) + '\n');
console.log(JSON.stringify(provenance));
