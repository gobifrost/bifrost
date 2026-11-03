// Public Git source only; preserves the separately qualified engine contribution.
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const [commit] = process.argv.slice(2);
if (!/^[a-f0-9]{40}$/.test(commit ?? '')) throw new Error('Exact SDK commit required');
const files = {}, sha256 = {};
for (const path of ['bifrost/client.py', 'bifrost/_tls.py', 'bifrost/_workspace_lock.py']) {
  const bytes = execFileSync('git', ['show', commit + ':api/' + path], {maxBuffer: 2 * 1024 * 1024});
  files[path] = {py: bytes.toString('utf8')};
  sha256[path] = createHash('sha256').update(bytes).digest('hex');
}
console.log('export default ' + JSON.stringify({commit, files, sha256}) + ';');
