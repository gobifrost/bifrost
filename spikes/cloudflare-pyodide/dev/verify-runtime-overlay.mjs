// Compare the actual prepared artifacts without publishing native-wheel bytes.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const [basePath, derivedPath] = process.argv.slice(2);
if (!basePath || !derivedPath) throw new Error('Supply original pinned base and derived runtime paths');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const baseBytes = await readFile(basePath);
const derivedBytes = await readFile(derivedPath);
assert.equal(hash(baseBytes), 'e1cca587e10cb755d49df0f2325c038a361a25f7031142a9e9e896a3c004bd39');
const decode = bytes => JSON.parse(bytes.toString('utf8').slice('export default '.length, -2));
const base = decode(baseBytes), derived = decode(derivedBytes);
assert.deepEqual(Object.keys(derived), Object.keys(base));
const changed = Object.keys(base).filter(key => JSON.stringify(base[key]) !== JSON.stringify(derived[key])).sort();
assert.deepEqual(changed, ['runtime_runner.py', 'src/services/execution/engine.py']);
assert.equal(derived['runtime_runner.py'].py,
  (await readFile(new URL('../backend/runtime-runner.py', import.meta.url), 'utf8')).replaceAll('\r\n', '\n'));
assert.equal(derived['src/services/execution/engine.py'].py,
  (await readFile(new URL('../../../api/src/services/execution/engine.py', import.meta.url), 'utf8')).replaceAll('\r\n', '\n'));
console.log(JSON.stringify({runtime_sha256: hash(derivedBytes), changed_modules: changed,
  preserved_modules: Object.keys(base).length - changed.length}));
