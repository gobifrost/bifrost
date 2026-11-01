import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,access,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {cloudProbeFetch,CLOUD_PROBE_URL,boundedText,boundedJson,diagnosticJson} from './probe-io.mjs';

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

test('diagnostic JSON rejects credential echoes including escaped nested values',()=>{
  for(const secret of ['synthetic-runtime-token','quoted"credential\nvalue']) {
    assert.throws(()=>diagnosticJson({body:{error:'echo '+secret}},[secret]),error=>{
      assert.equal(error.message,'Credential exposure: diagnostic evidence withheld');
      assert.ok(!error.message.includes(secret));
      return true;
    });
  }
});

test('serialized output limit includes JSON escaping and UTF-8 bytes',()=>{
  assert.throws(()=>diagnosticJson({body:'\u0000'.repeat(10)},[],40),/Serialized diagnostic exceeds/);
  const value={body:'✓'};
  const encoded=diagnosticJson(value);
  const bytes=new TextEncoder().encode(encoded).byteLength;
  assert.equal(diagnosticJson(value,[],bytes),encoded);
  assert.throws(()=>diagnosticJson(value,[],bytes-1),/byte limit/);
});

test('hostile response strings remain inert data and invalid guard configuration fails',()=>{
  const value={path:'../../outside',body:'import("node:fs").then(() => { throw 42; })'};
  assert.deepEqual(JSON.parse(diagnosticJson(value)),value);
  assert.throws(()=>diagnosticJson(value,[null]),TypeError);
  assert.throws(()=>diagnosticJson(value,[],Infinity),TypeError);
  assert.throws(()=>diagnosticJson(undefined),TypeError);
});

test('credential echo is rejected before a receipt is created; hostile fields cannot select its path',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'pyodide-diagnostic-'));
  const output=join(directory,'receipt.json');
  try {
    const secret='synthetic-scoped-credential';
    const body=await boundedJson(Response.json({error:'Bearer '+secret}));
    await assert.rejects(async()=>writeFile(output,diagnosticJson({body},[secret])),/evidence withheld/);
    await assert.rejects(access(output),{code:'ENOENT'});
    const hostile={path:'../../outside',body:'process.exit(99)'};
    await writeFile(output,diagnosticJson(hostile));
    assert.deepEqual(JSON.parse(await readFile(output,'utf8')),hostile);
  } finally {
    await rm(directory,{recursive:true});
  }
});
