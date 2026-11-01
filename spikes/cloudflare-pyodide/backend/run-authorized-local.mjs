// Optional local integration driver. No cloud calls or credentials on argv/disk.
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {writeFile} from 'node:fs/promises';
const exec=promisify(execFile);
const mode=process.argv.slice(2);
if(mode.length>1 || (mode.length===1 && mode[0]!=='--canonical')) throw new Error('Use --canonical or no arguments');
const canonical=mode[0]==='--canonical';
const args=['exec','bifrost-pyodide-test-tools-ddf316c1','env',
  'LOG_DIR=/tmp/bifrost-bifrost-test-79c5c14e','BIFROST_TEST_IMAGE_TAG=bifrost-test-79c5c14e',
  'docker','compose','-p','bifrost-test-79c5c14e','-f','docker-compose.test.yml','--profile','test',
  'run','--rm','--no-deps','--volume',
  '/run/desktop/mnt/host/c/Users/ThomasBray/.t3/worktrees/bifrost/t3-ddf316c1/spikes/cloudflare-pyodide:/spike:ro',
  'test-runner','python',canonical ? '/spike/backend/canonical-local.py' : '/spike/backend/authorized-local.py'];
let stdout;
let exitCode=0;
try { ({stdout}=await exec('docker',args,{maxBuffer:2*1024*1024,timeout:180000})); }
catch(error) { stdout=error.stdout ?? '';exitCode=typeof error.code==='number' ? error.code : 1; }
const line=stdout.split(/\r?\n/).find(value=>value.startsWith('{"runtime":'));
const report=line ? JSON.parse(line) : {error:'No qualification report returned'};
await writeFile(new URL(canonical ? '../results/canonical-local.json' : '../results/authorized-local-liveness.json',import.meta.url),
  JSON.stringify({process_exit_code:exitCode,...report},null,2)+'\n');
console.log({process_exit_code:exitCode,workflow_status:report.result?.result?.status,
  cross_tenant_status:report.cross_tenant_status,terminal_status:report.live_then_fixture_terminal_status,
  canonical_claim_verified:report.canonical_claim_verified});
process.exitCode=exitCode || (line ? 0 : 1);
