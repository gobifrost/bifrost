// A deployed closure is not automatically evidence for a later upstream source.
import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const [artifact, output] = process.argv.slice(2);
if (!artifact) throw new Error('Supply the prepared runtime artifact; optional output must be new');
const bytes = await readFile(artifact);
const hash = value => createHash('sha256').update(value).digest('hex');
assert.equal(hash(bytes), 'bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0');
const modules = JSON.parse(bytes.toString('utf8').slice('export default '.length, -2));
const namespaces = new Set(['src/__init__.py', 'src/core/__init__.py', 'src/models/__init__.py',
  'src/sdk/__init__.py', 'src/services/__init__.py', 'src/services/execution/__init__.py']);
const records = [], unexpected = [];
for (const [path, module] of Object.entries(modules)) {
  if (!path.endsWith('.py') || typeof module.py !== 'string') continue;
  if (!/^[a-zA-Z0-9_/.-]+$/.test(path) || path.split('/').includes('..') || path.startsWith('/')) {
    throw new Error('Runtime source path is outside the module namespace');
  }
  let source;
  try { source = await readFile(new URL('../../../api/' + path, import.meta.url), 'utf8'); }
  catch (error) { if (error.code === 'ENOENT') continue; throw error; }
  const sourceHash = hash(source.replaceAll('\r\n', '\n'));
  const packagedHash = hash(module.py);
  const state = sourceHash === packagedHash ? 'identical' :
    namespaces.has(path) && module.py === '# Runtime namespace; excludes server package initialization.\n' ?
      'explicit_namespace_adapter' : 'unqualified_source_drift';
  records.push({path: 'api/' + path, source_sha256: sourceHash, packaged_sha256: packagedHash, state});
  if (state === 'unqualified_source_drift') unexpected.push(path);
}
const sourceCommit = (await promisify(execFile)('git', ['rev-parse', 'HEAD'],
  {cwd: new URL('../../../', import.meta.url)})).stdout.trim();
const report = {source_commit: sourceCommit, artifact_sha256: hash(bytes), compared: records.length,
  identical: records.filter(row => row.state === 'identical').length,
  namespace_adapters: records.filter(row => row.state === 'explicit_namespace_adapter').length,
  unexpected, records, cloud_invocations: 0,
  scope: 'Tracked Python modules in this closure only; not the whole platform, deployed API revision or namespace export compatibility'};
if (output) await writeFile(output, JSON.stringify(report, null, 2) + '\n', {flag: 'wx'});
assert.deepEqual(unexpected, []);
assert.equal(records.length, 79);
console.log(JSON.stringify({source_commit: sourceCommit, compared: report.compared,
  identical: report.identical, namespace_adapters: report.namespace_adapters, unexpected}));
