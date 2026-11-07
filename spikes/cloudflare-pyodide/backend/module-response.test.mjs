import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifyModuleResponse} from './module-response.mjs';
const path='workflows/example.py';
const content='def workflow():\n    return "✓"\n';
const hash=createHash('sha256').update(content).digest('hex');
const fixture=(changes={})=>Response.json({path,content,hash,...changes});

test('pinned UTF-8 module is forwarded byte for byte',async()=>{
  const response=fixture();
  const expected=await response.clone().text();
  const result=await verifyModuleResponse(response,path,hash);
  assert.equal(await result.text(),expected);
});
test('changed source cannot execute under the old approved digest',async()=>{
  const changed='raise RuntimeError("changed")';
  const newHash=createHash('sha256').update(changed).digest('hex');
  await assert.rejects(verifyModuleResponse(fixture({content:changed,hash:newHash}),path,hash),/digest mismatch/);
});
test('server hash alone cannot authorize changed content',async()=>{
  await assert.rejects(verifyModuleResponse(fixture({content:'changed'}),path,hash),/digest mismatch/);
});
test('a different workspace path is refused',async()=>{
  await assert.rejects(verifyModuleResponse(fixture({path:'other.py'}),path,hash),/identity mismatch/);
});
test('an unpinned successful response is refused',async()=>{
  await assert.rejects(verifyModuleResponse(fixture(),path,undefined),/Unpinned/);
});
test('oversized streamed response is canceled before it is delivered',async()=>{
  let canceled=false;
  const stream=new ReadableStream({pull(c){c.enqueue(new Uint8Array(12));},cancel(){canceled=true;}});
  await assert.rejects(verifyModuleResponse(new Response(stream),path,hash,{maxBytes:10}),/exceeds limit/);
  assert.equal(canceled,true);
});
test('real auth challenges retain status, headers and body without replay',async()=>{
  const response=new Response('denied',{status:401,headers:{'WWW-Authenticate':'Bearer error="invalid_token"'}});
  const result=await verifyModuleResponse(response,path,undefined);
  assert.equal(result,response);
  assert.equal(result.headers.get('WWW-Authenticate'),'Bearer error="invalid_token"');
  assert.equal(await result.text(),'denied');
});
