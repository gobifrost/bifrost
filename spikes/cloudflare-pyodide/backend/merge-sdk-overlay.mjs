// Merge exact public source/wheel data; no engine semantics or credentials.
import {readFileSync, writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const [basePath, overlayPath, outputPath] = process.argv.slice(2);
assert.ok(basePath && overlayPath && outputPath && basePath !== outputPath);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const decode = bytes => JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, '').trim().slice('export default '.length).replace(/;$/, ''));
const baseBytes = readFileSync(basePath), overlay = decode(readFileSync(overlayPath));
assert.equal(hash(baseBytes), 'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
assert.equal(overlay.commit, '139596497d57b219ddcc3616aa66ec58e1b3cf3c');
assert.equal(overlay.dependency.wheel_sha256, '3cb2210c8f88ba2318d29b0388d1023c8492ff72ecdde4ebdaddbb13a31b1c4a');
const modules = decode(baseBytes), original = {...modules};
for (const [name, value] of Object.entries(overlay.files)) {
  assert.ok(['bifrost/client.py', 'bifrost/_tls.py', 'bifrost/_workspace_lock.py'].includes(name)
    || name.startsWith('certifi/') || name.startsWith('certifi-2026.4.22.dist-info/'));
  const bytes = value.py === undefined ? Buffer.from(value.base64, 'base64') : Buffer.from(value.py);
  assert.equal(hash(bytes), overlay.sha256[name], name);
  modules[name] = value;
}
assert.ok(Object.keys(overlay.files).some(name => /dist-info\/.*LICENSE/.test(name)));
for (const [name, value] of Object.entries(original)) {
  if (!Object.hasOwn(overlay.files, name)) assert.equal(modules[name], value, name);
}
const output = 'export default ' + JSON.stringify(modules) + ';\n';
writeFileSync(outputPath, output);
console.log(JSON.stringify({base_sha256: hash(baseBytes), bundle_sha256: hash(output),
  bundle_bytes: Buffer.byteLength(output), sdk_commit: overlay.commit, dependency: overlay.dependency,
  overlay_sha256: overlay.sha256, preserved_module_count: Object.keys(original).filter(name => !Object.hasOwn(overlay.files, name)).length,
  module_count: Object.keys(modules).length}, null, 2));
