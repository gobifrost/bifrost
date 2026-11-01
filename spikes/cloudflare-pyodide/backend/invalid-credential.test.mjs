import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {boundedText,diagnosticJson} from '../probe-io.mjs';
const response=await fetch('http://127.0.0.1:8797',{
  method:'POST',body:JSON.stringify({action:'invalid_credential'}),signal:AbortSignal.timeout(60000),
});
const text=await boundedText(response);
let body;
try { body=JSON.parse(text); } catch { body={unparsed:text}; }
const evidence={runtime:'local-workerd',api:'isolated-debug-19e8149c',status:response.status,body};
await writeFile(new URL('../results/current-runtime-invalid-credential.json',import.meta.url),
  diagnosticJson(evidence,['synthetic-invalid-runtime-token']));
console.log({status:response.status,api_statuses:body.api_statuses,result:body.result});
assert.equal(response.status,200);
assert.deepEqual(body.api_statuses,[401],'Actual upstream API must reject the supplied synthetic token');
assert.equal(body.result.status,'Failed');
assert.equal(body.result.error_type,'WorkflowLoadError');
assert.match(body.result.error_message,/module fetch returned 401/);
assert.equal(body.result.result,null);
assert.ok(!text.includes('synthetic-invalid-runtime-token'),'Credential must not appear in the result');
