import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import test from 'node:test';

// Exercise the actual probe finally blocks without opening a port or a Worker.
const hook = `
import fs from 'node:fs';
import http from 'node:http';
import {EventEmitter} from 'node:events';
import {syncBuiltinESMExports} from 'node:module';
let closed=false;
http.createServer=()=>Object.assign(new EventEmitter(),{
  listen(_port,_host,ready){ready();},
  closeAllConnections(){closed=true;},
  close(done){done();},
});
fs.writeFileSync=()=>{throw new Error('synthetic receipt write failure');};
syncBuiltinESMExports();
globalThis.fetch=async()=>Response.json({}, {status:500});
process.on('exit',()=>{console.log('SERVER_CLOSED='+closed);if(!closed)process.exitCode=77;});
`;
for (const name of ['approved-httpx-probe.mjs', 'cooperative-http-probe.mjs', 'https-disconnect-probe.mjs']) {
  test(`${name} closes server even when receipt writing fails`, () => {
    const child = spawnSync(process.execPath, ['--import', 'data:text/javascript;base64,'+Buffer.from(hook).toString('base64'),
      fileURLToPath(new URL(name, import.meta.url))], {encoding: 'utf8', timeout: 10000});
    assert.equal(child.error, undefined);
    assert.equal(child.status, 1);
    assert.match(child.stderr, /synthetic receipt write failure/);
    assert.match(child.stdout, /SERVER_CLOSED=true/);
  });
}
