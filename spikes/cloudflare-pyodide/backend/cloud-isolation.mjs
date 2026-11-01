// Authorized bounded cloud experiment. Never log auth; ambiguous calls consume budget.
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {cloudProbeFetch,boundedText,diagnosticJson} from '../probe-io.mjs';
const {SPIKE_AUTH}=JSON.parse(await readFile(join(tmpdir(),'bifrost-pyodide-ddf316c1-secrets.json'),'utf8'));
const ledgerPath=new URL('../results/fresh-cloud-budget.json',import.meta.url);
let ledger;
try { ledger=JSON.parse(await readFile(ledgerPath,'utf8')); }
catch(error) { if(error.code!=='ENOENT') throw error; ledger={max_invocations:20,incremental_usd_cap:0.10,reservations:[]}; }
const results=[];
const suite=process.env.SPIKE_SUITE==='engine' ? 'engine' : 'isolation';
let sequence=Promise.resolve();
async function reserve(body) {
  const operation=sequence.then(async()=>{
    if(ledger.reservations.length>=20) throw new Error('Authorized fresh-worker budget exhausted');
    const record={ordinal:ledger.reservations.length+1,at:new Date().toISOString(),request:body};
    ledger.reservations.push(record);
    await writeFile(ledgerPath,JSON.stringify(ledger,null,2)+'\n');
    return record.ordinal;
  });
  sequence=operation;
  return operation;
}
async function probe(body) {
  const ordinal=await reserve(body);
  const started=performance.now();
  const record={ordinal,request:body};
  results.push(record);
  try {
    const response=await cloudProbeFetch(SPIKE_AUTH,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body),signal:AbortSignal.timeout(60000)});
    record.status=response.status;
    const text=await boundedText(response);
    try { record.body=JSON.parse(text); } catch { record.body={unparsed:text.slice(0,16000)}; }
  } catch(error) { record.transport_error={type:error.name,message:String(error)}; }
  record.client_wall_ms=performance.now()-started;
  console.log({ordinal,action:body.action,status:record.status,outcome:record.body?.outcome,wall_ms:record.client_wall_ms});
  return record;
}
const evidence={runtime:'deployed-cloudflare-dynamic-python-worker',started_at:new Date().toISOString(),
  versions:process.env.SPIKE_VERSION ?? 'recorded-in-deploy-log',results};
try {
  const unauth=await cloudProbeFetch(undefined,{method:'POST',body:'{"action":"state"}'});
  evidence.unauthenticated_status=unauth.status;
  await unauth.body?.cancel();
  if(unauth.status!==403) throw new Error('Authentication gate not established');
  if(['cpu_limit','cpu_catch'].includes(process.env.SPIKE_ONLY)) {
    await probe({action:process.env.SPIKE_ONLY});
  } else if(suite==='engine') {
    for(const fixture of (process.env.SPIKE_ONLY==='transform' ? ['transform'] : ['transform','async','failure'])) await probe({fixture});
  } else {
  await probe({action:'state',marker:'cloud-tenant-A'});
  await probe({action:'state',marker:'cloud-tenant-B'});
  await Promise.all([probe({action:'state',marker:'cloud-parallel-A'}),probe({action:'state',marker:'cloud-parallel-B'})]);
  await probe({action:'egress'});
  await probe({action:'io',cancel:true});
  await probe({action:'cpu',cancel:true});
  }
} finally {
  evidence.finished_at=new Date().toISOString();
  evidence.total_reserved_invocations=ledger.reservations.length;
  const filename=['cpu_limit','cpu_catch'].includes(process.env.SPIKE_ONLY) ? process.env.SPIKE_ONLY.replace('_','-')+'-cloud-fresh' :
    (suite==='engine' ? (process.env.SPIKE_ONLY==='transform' ? 'engine-cloud-diagnostic' : 'engine-cloud-fresh') : 'isolation-cloud-fresh');
  await writeFile(new URL('../results/'+filename+'.json',import.meta.url),diagnosticJson(evidence,[SPIKE_AUTH]));
}
