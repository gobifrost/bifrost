import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cloudProbeFetch,CLOUD_PROBE_URL,boundedText,boundedJson} from './probe-io.mjs';

test('authentication goes only to the fixed target and redirects fail closed',async()=>{
  const original=globalThis.fetch;
  const calls=[];
  globalThis.fetch=async(url,init)=>{calls.push({url,init});return new Response('{}');};
  try {
    const secret=Buffer.alloc(32,7).toString('base64');
    await cloudProbeFetch(secret,{method:'POST',redirect:'follow'},'/state_read');
    assert.equal(calls[0].url,CLOUD_PROBE_URL+'/state_read');
    assert.equal(calls[0].init.redirect,'error');
    assert.equal(calls[0].init.headers.get('X-Spike-Auth'),secret);
    for(const path of ['//other.invalid','/../other','https://other.invalid','/state?redirect=x']) {
      assert.throws(()=>cloudProbeFetch(secret,{},path),/Invalid fixed-probe path/);
    }
    assert.throws(()=>cloudProbeFetch('not-a-probe-credential'),/Invalid temporary/);
    assert.equal(calls.length,1);
  } finally { globalThis.fetch=original; }
});
test('response limit counts UTF-8 bytes, not JavaScript characters',async()=>{
  assert.equal(await boundedText(new Response('✓'),3),'✓');
  await assert.rejects(boundedText(new Response('✓'),2),/byte limit/);
});
test('oversized streaming response is canceled before more data is buffered',async()=>{
  let canceled=false;
  const response=new Response(new ReadableStream({
    pull(controller){controller.enqueue(new Uint8Array(20));},
    cancel(){canceled=true;},
  }));
  await assert.rejects(boundedText(response,10),/byte limit/);
  assert.equal(canceled,true);
});
test('invalid UTF-8 and malformed JSON are rejected',async()=>{
  await assert.rejects(boundedText(new Response(new Uint8Array([0xff]))),TypeError);
  await assert.rejects(boundedJson(new Response('{invalid')),SyntaxError);
  assert.deepEqual(await boundedJson(Response.json({status:'Failed'})),{status:'Failed'});
});
