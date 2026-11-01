// Execute only after guest ABI/assets qualify; no cloud mode exists.
import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import {boundedText} from '../probe-io.mjs';
import {withExclusiveReceipt} from './exclusive-receipt.mjs';
import {classifyTimedCancellation} from './termination-evidence.mjs';
if(process.argv.slice(2).join(' ')!=='--run') throw new Error('Explicit --run required after ABI qualification');
const receipt=new URL('../results/guest-termination-timed.json',import.meta.url);
await withExclusiveReceipt(receipt,async save=>{
  const records=[];
  let active;
  const server=createServer(async(request,response)=>{
    try {
      if(request.method!=='POST' || request.url!=='/effect') return response.writeHead(404).end();
      const text=await boundedText(new Response(request),256);
      const body=JSON.parse(text);
      if(!active || body.id!==active.id || !['before','caught','after'].includes(body.event) || active.events.length>=16) {
        return response.writeHead(422).end();
      }
      active.events.push({event:body.event,elapsed_ms:performance.now()-active.started});
      response.end('ok');
      if(body.event==='before' && active.mode==='abort') {
        const current=active;
        current.timer=setTimeout(()=>{
          current.events.push({event:'abort_requested',elapsed_ms:performance.now()-current.started});
          current.controller.abort('fixed diagnostic request abort');
        },10);
      }
    } catch { response.writeHead(400).end(); }
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(8820,'127.0.0.1',resolve);});
  try {
    for(const mode of ['control','catch','abort']) {
      active={id:randomUUID(),mode,events:[],started:performance.now(),controller:new AbortController()};
      try {
        const response=await fetch('http://127.0.0.1:8811',{
          method:'POST',body:JSON.stringify({id:active.id,mode}),
          signal:AbortSignal.any([active.controller.signal,AbortSignal.timeout(20000)])});
        active.outcome={status:response.status,body:await boundedText(response,65536)};
      } catch(error) { active.outcome={error_type:error.name}; }
      await new Promise(resolve=>setTimeout(resolve,5000));
      clearTimeout(active.timer);
      const record={id:active.id,mode,events:active.events,outcome:active.outcome,
        ...classifyTimedCancellation(active.events)};
      records.push(record);
      await save({runtime:'local-workerd',records,cloud_invocations:0,physical_termination_verified:false,
        canonical_late_result_fencing_verified:false,execution_admission_qualified:false});
      if(mode==='control' && !record.events.some(e=>e.event==='after')) {
        throw new Error('Completion negative control failed; abort case not invoked');
      }
    }
  } finally {
    clearTimeout(active?.timer);
    active=undefined;
    await new Promise(resolve=>server.close(resolve));
  }
});
