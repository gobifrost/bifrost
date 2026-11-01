"""Run selected unchanged pydantic-core test modules in official Pyodide.

Not Cloudflare runtime acceptance; shared upstream conftest/GC fixtures are outside this subset.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
RUN = ROOT / 'runs/pydantic-core-upstream-tests-resume'
CORE = ROOT / 'runs/pyodide-package-qualification-resume/core/pyodide'
SOURCE = ROOT / 'runs/pydantic-core-source-inspection/source.tar.gz'
PACKAGES = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide/python_modules'
NAME = 'pyodide-ddf316c1-upstream-tests'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Wrong guest')
    if subprocess.check_output(['docker', 'ps', '-q'], text=True).strip():
        raise RuntimeError('Reconcile active containers first')
    RUN.mkdir()
    (RUN / 'tests').mkdir()
    (RUN / 'test-deps').mkdir()
    report = {'runtime': 'official-pyodide-on-node', 'cloudflare_runtime_qualified': False,
              'physical_termination_verified': False, 'cloud_invocations': 0,
              'started_at_epoch': time.time(), 'test_source_sha256': {}, 'test_dependencies': []}
    try:
        expected = '1b0e8dc3932cf4e9ed5727570325cacc716b9409ffc794a221c0ccc72e8eb775'
        if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Upstream source archive changed')
        report['upstream_source_archive_sha256'] = expected
        with tarfile.open(SOURCE) as archive:
            for name in ('tests/serializers/test_none.py', 'tests/serializers/test_json.py'):
                data = archive.extractfile('pydantic_core-2.50.2/' + name).read()
                (RUN / 'tests' / Path(name).name).write_bytes(data)
                report['test_source_sha256'][name] = hashlib.sha256(data).hexdigest()
        lock = json.loads((CORE / 'pyodide-lock.json').read_text())['packages']
        needed = {'pytest', 'packaging'}
        pending = list(needed)
        while pending:
            for dep in lock[pending.pop()]['depends']:
                if dep not in needed:
                    needed.add(dep)
                    pending.append(dep)
        for name in sorted(needed):
            item = lock[name]
            url = 'https://cdn.jsdelivr.net/pyodide/v314.0.6/full/' + item['file_name']
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(25 * 1024**2 + 1)
            if len(data) > 25 * 1024**2 or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise RuntimeError('Test dependency hash mismatch')
            wheel = RUN / item['file_name']
            wheel.write_bytes(data)
            with zipfile.ZipFile(wheel) as archive:
                for entry in archive.infolist():
                    path = Path(entry.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in entry.filename:
                        raise RuntimeError('Unsafe test wheel')
                archive.extractall(RUN / 'test-deps')
            report['test_dependencies'].append({'name': name, 'version': item['version'], 'url': url, 'sha256': item['sha256']})
        driver = '''import {loadPyodide} from '/core/pyodide.mjs';
import {writeFileSync} from 'node:fs';
const py=await loadPyodide();
for(const [name,root] of [['packages','/packages'],['test-deps','/qualification/test-deps'],['tests','/qualification/tests'],['receipts','/qualification']]) {
 py.FS.mkdir('/'+name); py.FS.mount(py.FS.filesystems.NODEFS,{root},'/'+name);
}
const result=await py.runPythonAsync(`
import sys,os,json
sys.path[:0]=['/packages','/test-deps']
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
os.chdir('/tests')
import pytest,pyodide,pydantic_core
code=pytest.main(['.','-q','--junitxml=/receipts/junit.xml'])
json.dumps({'exit_code':int(code),'pyodide':pyodide.__version__,'pydantic_core':pydantic_core.__version__})
`);
writeFileSync('/qualification/observed.json',result+'\\n',{flag:'wx'});
if(JSON.parse(result).exit_code!==0)process.exitCode=1;
'''
        (RUN / 'tests.mjs').write_text(driver)
        args = ['docker', 'run', '--name', NAME, '--network=none', '--cpus=1', '--memory=1g',
                '--memory-swap=1g', '--pids-limit=128', '--mount', f'type=bind,src={RUN},dst=/qualification',
                '--mount', f'type=bind,src={CORE},dst=/core,readonly',
                '--mount', f'type=bind,src={PACKAGES},dst=/packages,readonly', '--workdir', '/qualification',
                '--entrypoint', 'node', 'bifrost-test-api-dev:bifrost-test-7ddfac36', 'tests.mjs']
        report['command'] = args
        with (RUN / 'pytest.log').open('x') as log:
            result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        report['exit_code'] = result.returncode
        if (RUN / 'observed.json').exists():
            report['observed'] = json.loads((RUN / 'observed.json').read_text())
    except Exception as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        subprocess.run(['docker', 'stop', '-t', '3', NAME], capture_output=True)
        report['finished_at_epoch'] = time.time()
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
