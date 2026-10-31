// Uses only the dedicated local debug API; token lives in memory, never in artifacts.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const api='http://127.0.0.1:39046';
const login = await fetch(`${api}/api/auth/login`, {method:'POST',
  body:new URLSearchParams({username:'dev@gobifrost.com', password:'password'})});
assert.equal(login.status,200);
const {access_token: token} = await login.json();
assert.ok(token, 'dedicated debug login must return a token');
const headers={'Content-Type':'application/json', Authorization:`Bearer ${token}`};
let response=await fetch(`${api}/api/integrations`,{headers});
const listing=await response.json();
const items=Array.isArray(listing)?listing:(listing.items ?? listing.integrations);
let integration=items.find(i=>i.name==='pyodide-spike');
if (!integration) {
  response=await fetch(`${api}/api/integrations`, {method:'POST',headers,
    body:JSON.stringify({name:'pyodide-spike',config_schema:[{key:'answer',type:'int'}]})});
  assert.equal(response.status,201);
  integration=await response.json();
}
assert.ok(integration);
response=await fetch(`${api}/api/integrations/${integration.id}/config`, {method:'PUT', headers,
  body:JSON.stringify({config:{answer:42}})});
assert.equal(response.status,200,await response.text());
process.env.SPIKE_TOKEN=token;
process.env.SPIKE_OUTPUT='results/benchmark-sdk.json';
await import('./bench.mjs');
// Independent REST readback verifies the SDK write reached the real platform.
response=await fetch(`${api}/api/tables/pyodide-spike/documents/probe`,{headers});
const readback=await response.json();
await writeFile('results/sdk-readback.json',JSON.stringify({http_status:response.status,readback},null,2)+'\n');
assert.equal(response.status,200);
assert.equal(readback.data.value,42);
console.log('Real SDK write independently read back through REST.');
