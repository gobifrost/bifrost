"""Actual upstream engine semantics on guest Pyodide; no mocked or live SDK calls."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
RUN = ROOT / 'runs/engine-semantics-resume-v3'
CORE = ROOT / 'runs/pyodide-package-qualification-resume/core/pyodide'
SPIKE = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide'
NAME = 'pyodide-ddf316c1-engine-semantics-v3'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Wrong guest')
    if subprocess.check_output(['docker', 'ps', '-q'], text=True).strip():
        raise RuntimeError('Reconcile active containers first')
    RUN.mkdir()
    result = {'source': '785b560b9973020e1c13401383e1a73a87a54940',
              'runtime': 'official-pyodide-on-node', 'cloudflare_runtime_qualified': False,
              'sdk_operation_qualified': False, 'physical_termination_verified': False,
              'cloud_invocations': 0, 'started_at_epoch': time.time()}
    try:
        result['engine_sha256'] = hashlib.sha256((SPIKE / 'backend/runtime-source/src/services/execution/engine.py').read_bytes()).hexdigest()
        driver = '''import {loadPyodide} from '/core/pyodide.mjs';
import {writeFileSync} from 'node:fs';
const py=await loadPyodide();
for(const [name,root] of [['packages','/spike/python_modules'],['source','/spike/backend/runtime-source'],['backend','/spike/backend']]) {
 py.FS.mkdir('/'+name);py.FS.mount(py.FS.filesystems.NODEFS,{root},'/'+name);
}
try {
const result=await py.runPythonAsync(`
import sys,json,asyncio,dataclasses
sys.path[:0]=['/source','/packages','/backend']
from runtime_guard import require_runtime
abi=require_runtime()
from src.core.module_cache_sync import mark_execution_child
mark_execution_child()
from src.services.execution.engine import ExecutionRequest,execute
from src.sdk.context import Caller,ExecutionContext
from bifrost.decorators import workflow

@workflow
async def transform(context:ExecutionContext,values):
    return {'total':sum(values),'execution_id':context.execution_id}
@workflow
async def asynchronous(context:ExecutionContext,value):
    await asyncio.sleep(0)
    return {'value':value,'scope':context.scope}
@workflow
async def partial():
    return {'success':False,'reason':'fixed qualification'}
@workflow
async def failure():
    raise ValueError('credential=synthetic-engine-redaction-marker')

observations=[]
for name,func,parameters in [('json',transform,{'values':[1,2,3]}),('async',asynchronous,{'value':42}),
                             ('partial',partial,{}),('failure',failure,{})]:
    request=ExecutionRequest(execution_id='00000000-0000-0000-0000-000000000001',
        caller=Caller('00000000-0000-0000-0000-000000000002','probe@example.invalid','Probe'),
        organization=None,func=func,name=name,parameters=parameters,
        transient=True,no_cache=True,public_url='https://qualification.invalid',
        engine_token='synthetic-engine-redaction-marker')
    observed=dataclasses.asdict(await execute(request))
    observations.append({'name':name,'result':observed})
from pathlib import Path
Path('/observations.json').write_text(json.dumps({'abi':abi,'observations':observations}))
assert observations[0]['result']['result']['total']==6
assert observations[1]['result']['result']=={'value':42,'scope':'GLOBAL'}
assert observations[2]['result']['status']=='CompletedWithErrors'
assert observations[3]['result']['status']=='Failed'
assert observations[3]['result']['error_type']=='ValueError'
assert 'synthetic-engine-redaction-marker' not in json.dumps(observations)
assert '[REDACTED]' in observations[3]['result']['error_message']
json.dumps({'abi':abi,'observations':observations})
`);
writeFileSync('/qualification/observed.json',result+'\\n',{flag:'wx'});
console.log('Actual upstream engine semantics completed');
} catch(error) {
 if(py.FS.analyzePath('/observations.json').exists) {
   writeFileSync('/qualification/partial-observed.json',py.FS.readFile('/observations.json'),{flag:'wx'});
 }
 writeFileSync('/qualification/error.txt',String(error.stack).slice(-16000),{flag:'wx'});
 console.error(error.message);process.exitCode=1;
}
'''
        (RUN / 'engine.mjs').write_text(driver)
        args = ['docker', 'run', '--name', NAME, '--network=none', '--cpus=1', '--memory=1g',
                '--memory-swap=1g', '--pids-limit=128', '--mount', f'type=bind,src={RUN},dst=/qualification',
                '--mount', f'type=bind,src={CORE},dst=/core,readonly',
                '--mount', f'type=bind,src={SPIKE},dst=/spike,readonly', '--workdir', '/qualification',
                '--entrypoint', 'node', 'bifrost-test-api-dev:bifrost-test-7ddfac36', 'engine.mjs']
        result['command'] = args
        with (RUN / 'engine.log').open('x') as log:
            outcome = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        result['exit_code'] = outcome.returncode
        if (RUN / 'observed.json').exists():
            result['observed'] = json.loads((RUN / 'observed.json').read_text())
    finally:
        subprocess.run(['docker', 'stop', '-t', '3', NAME], capture_output=True)
        result['finished_at_epoch'] = time.time()
        (RUN / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k:v for k,v in result.items() if k!='observed'}))


if __name__ == '__main__':
    main()
