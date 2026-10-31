import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {writeFile} from 'node:fs/promises';
import {boundedText} from '../probe-io.mjs';
const controller=new AbortController();
const events=[];
let abortTimer;
let after;
const afterEffect=new Promise(resolve=>{after=resolve;});
const started=performance.now();
const server=createServer(async(request,response)=>{
  const chunks=[];
  let bytes=0;
  for await(const chunk of request) {
    bytes+=chunk.length;
    if(bytes>32) { response.writeHead(413).end();request.destroy();return; }
    chunks.push(chunk);
  }
  const event=Buffer.concat(chunks).toString();
  if(!['before','after'].includes(event)) { response.writeHead(400).end();return; }
  events.push({event,elapsed_ms:performance.now()-started});
  response.end('ok');
  // Callback responds first: the Python child resumes its CPU loop, while this
  // timer runs independently of workerd's event loop.
  if(event==='before') abortTimer=setTimeout(()=>controller.abort('external diagnostic abort'),10);
  if(event==='after') after();
});
await new Promise(resolve=>server.listen(8800,'127.0.0.1',resolve));
let observationTimer;
let outcome;
try {
  try {
    const response=await fetch('http://127.0.0.1:8798',{
      method:'POST',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(45000)]),
    });
    outcome={status:response.status,body:await boundedText(response)};
  } catch(error) { outcome={error_type:error.name ?? typeof error,error:String(error)}; }
  await Promise.race([afterEffect,new Promise(resolve=>{observationTimer=setTimeout(resolve,15000);})]);
  const result={runtime:'local-workerd',external_abort_requested:controller.signal.aborted,
    observation_window_ms:15000,events,outcome};
  await writeFile(new URL('../results/external-cpu-cancel-local.json',import.meta.url),JSON.stringify(result,null,2)+'\n');
  console.log({external_abort_requested:result.external_abort_requested,events,outcome_status:outcome.status,
    outcome_error:outcome.error_type});
  assert.ok(controller.signal.aborted,'External timer must fire');
  assert.ok(events.some(value=>value.event==='before'),'Child must start');
  assert.ok(!events.some(value=>value.event==='after'),'Aborted CPU run must not emit a later effect');
} finally {
  clearTimeout(abortTimer);
  clearTimeout(observationTimer);
  await new Promise(resolve=>server.close(resolve));
}
