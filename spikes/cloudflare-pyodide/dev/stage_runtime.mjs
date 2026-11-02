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
await writeFile(new URL('generated-runtime.mjs', directory), bytes);
await writeFile(new URL('runtime-code.mjs', directory), await readFile(new URL('../backend/runtime-code.mjs', import.meta.url)));
console.log(JSON.stringify({runtime_sha256: digest, upstream_source: '785b560b9973020e1c13401383e1a73a87a54940',
  note: 'No api/src or api/bifrost changes between prepared source and dev source 2037ac0'}));
