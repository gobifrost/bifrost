// Exercise the actual upstream synchronous cold-module HTTP client in Pyodide.
// No credentials and no egress: this identifies transport vs network denial.
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {boundedJson} from '../probe-io.mjs';
const response=await fetch('http://127.0.0.1:8797',{
  method:'POST',body:JSON.stringify({action:'sync_transport'}),signal:AbortSignal.timeout(60000),
});
const body=await boundedJson(response);
const result={runtime:'local-workerd',status:response.status,body};
await writeFile(new URL('../results/current-runtime-sync-transport.json',import.meta.url),JSON.stringify(result,null,2)+'\n');
console.log(result);
assert.equal(response.status,200);
assert.equal(body.error_type,'ConnectError');
assert.match(body.error,/not permitted to access the internet/);
