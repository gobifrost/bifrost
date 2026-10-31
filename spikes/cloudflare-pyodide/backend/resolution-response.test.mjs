import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifyResolutionResponse} from './resolution-response.mjs';
const content='def fold(n): return n + 1';
const hash=createHash('sha256').update(content).digest('hex');
const module={kind:'module',path:'helper.py',storage_path:'helper.py',content,hash};
test('upstream resolver response survives pinned module verification',async()=>{
  const response=await verifyResolutionResponse(Response.json(module),{'helper.py':hash});
  assert.deepEqual(await response.json(),module);
});
test('unapproved module and changed source are refused',async()=>{
  await assert.rejects(verifyResolutionResponse(Response.json(module),{}),/Unpinned/);
  await assert.rejects(verifyResolutionResponse(Response.json({...module,content:'changed'}),{'helper.py':hash}),/digest mismatch/);
});
test('namespace requires an approved child and scoped paths are explicit boundary',async()=>{
  const namespace={kind:'namespace',path:'modules'};
  assert.deepEqual(await (await verifyResolutionResponse(Response.json(namespace),{'modules/helper.py':hash})).json(),namespace);
  await assert.rejects(verifyResolutionResponse(Response.json(namespace),{}),/Unpinned namespace/);
  await assert.rejects(verifyResolutionResponse(Response.json({...module,storage_path:'_solutions/id/helper.py'}),{}),/Scoped resolution/);
});
test('not-found and authentication failure semantics are unchanged',async()=>{
  const miss={kind:'not_found',path:'missing'};
  assert.deepEqual(await (await verifyResolutionResponse(Response.json(miss),{})).json(),miss);
  const denied=new Response('denied',{status:401});
  assert.equal(await verifyResolutionResponse(denied,{}),denied);
});
