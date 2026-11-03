import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

test('separate canonical candidate uses RPC and refuses to map coroutine receipt to terminal', () => {
  const directory = mkdtempSync(join(tmpdir(), 'canonical-cooperative-'));
  const https = join(directory, 'https.mjs'), output = join(directory, 'cooperative.mjs');
  const script = name => fileURLToPath(new URL(name, import.meta.url));
  try {
    execFileSync(process.execPath, [script('prepare-canonical-https.mjs'), https]);
    const original = readFileSync(https);
    execFileSync(process.execPath, [script('prepare-canonical-cooperative.mjs'), https, output]);
    execFileSync(process.execPath, ['--check', output]);
    const candidate = readFileSync(output, 'utf8');
    assert.match(candidate, /entrypoint\.run_execution/);
    assert.match(candidate, /const response=cooperativeRpcResponse\(rpcWire\)/);
    assert.doesNotMatch(candidate, /rpc=JSON\.parse/);
    assert.match(candidate, /execution_settled:false/);
    assert.match(candidate, /cooperativeEntries\.delete\(id\)/);
    assert.match(candidate, /httpsExecutions\.get\(handle\)!==control\.execution_id/);
    assert.match(candidate, /cooperativeEntries\.get\(handle\)\.requested=true/);
    assert.doesNotMatch(candidate, /cooperativeEntries\.get\(handle\)\.cancel_execution/);
    assert.match(candidate, /await entrypoint\.cancel_execution\(body\.execution_id\)/);
    assert.match(candidate, /finally\{monitorRunning=false;await monitor;\}/);
    assert.deepEqual(readFileSync(https), original);
    assert.throws(() => execFileSync(process.execPath, [script('prepare-canonical-cooperative.mjs'), https, output], {stdio: 'pipe'}));
  } finally {rmSync(directory, {recursive: true, force: true});}
});
