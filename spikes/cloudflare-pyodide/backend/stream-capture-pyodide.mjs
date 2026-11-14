// Actual upstream engine with the tracked engine patch; no SDK/network calls.
import {loadPyodide} from '/core/pyodide.mjs';
import {writeFileSync} from 'node:fs';
const py = await loadPyodide();
for (const [name, root] of [['packages', '/spike/python_modules'], ['source', '/spike/backend/runtime-source'], ['backend', '/spike/backend'], ['patch', '/patch']]) {
  py.FS.mkdir('/' + name);
  py.FS.mount(py.FS.filesystems.NODEFS, {root}, '/' + name);
}
const output = await py.runPythonAsync(`
import sys,json,dataclasses,importlib.util
sys.path[:0]=['/source','/packages','/backend']
from runtime_guard import require_runtime
abi=require_runtime()
from src.core.module_cache_sync import mark_execution_child
mark_execution_child()
import src.services.execution
spec=importlib.util.spec_from_file_location('src.services.execution.engine','/patch/engine.py')
engine=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=engine
spec.loader.exec_module(engine)
src.services.execution.engine=engine
from src.sdk.context import Caller
from bifrost.decorators import workflow
TOKEN='synthetic-stream-runtime-secret-0123456789'
@workflow
async def success():
    print('stdout='+TOKEN)
    print('stderr='+TOKEN,file=sys.stderr)
    return 70
@workflow
async def failure():
    print('before failure='+TOKEN,end='')
    raise ValueError('failure='+TOKEN)
records=[]
for name,func in [('success',success),('failure',failure)]:
    # Empty execution ID intentionally disables log delivery: this fixture
    # proves actual engine return/redaction semantics, not API authorization.
    request=engine.ExecutionRequest(execution_id='',caller=Caller('probe','probe@example.invalid','Probe'),
        organization=None,func=func,engine_token=TOKEN,no_cache=True,public_url='https://platform.invalid')
    result=dataclasses.asdict(await engine.execute(request))
    records.append({'name':name,'result':result})
assert records[0]['result']['status']=='Success'
assert records[0]['result']['result']==70
assert [row['message'] for row in records[0]['result']['logs']]==['stdout=[REDACTED]','stderr=[REDACTED]']
assert records[1]['result']['status']=='Failed'
assert records[1]['result']['error_type']=='ValueError'
assert 'before failure=[REDACTED]' in [row['message'] for row in records[1]['result']['logs']]
assert TOKEN not in json.dumps(records)
json.dumps({'abi':abi,'records':records,'sdk_calls':0,'cloud_runtime_qualified':False,'physical_stop_qualified':False})
`);
writeFileSync('/qualification/pyodide.json', output + '\n', {flag: 'wx'});
console.log('Actual guest Pyodide engine preserves and redacts printed output on success and failure.');
