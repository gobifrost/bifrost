"""Exercise upstream PyYAML native scanner/parser/emitter cases in official Pyodide."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
RUN = ROOT / 'runs/pyyaml-upstream-tests-resume-v3'
CORE = ROOT / 'runs/pyodide-package-qualification-resume/core/pyodide'
SOURCE = ROOT / 'runs/pyyaml-source-inspection/source.tar.gz'
PACKAGES = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide/python_modules'
NAME = 'pyodide-ddf316c1-pyyaml-tests-v3'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Wrong guest')
    if subprocess.check_output(['docker', 'ps', '-q'], text=True).strip():
        raise RuntimeError('Reconcile active containers first')
    RUN.mkdir()
    report = {'runtime': 'official-pyodide-on-node', 'cloudflare_runtime_qualified': False,
              'physical_termination_verified': False, 'cloud_invocations': 0,
              'started_at_epoch': time.time()}
    try:
        digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        if digest != 'd76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f':
            raise RuntimeError('PyYAML source changed')
        report['source_archive_sha256'] = digest
        with tarfile.open(SOURCE) as archive:
            for item in archive.getmembers():
                if not (item.isfile() or item.isdir()) or Path(item.name).is_absolute() or '..' in Path(item.name).parts:
                    raise RuntimeError('Unsafe upstream source archive')
            archive.extractall(RUN / 'source', filter='data')
        tests = RUN / 'source/pyyaml-6.0.3/tests/legacy_tests'
        report['upstream_test_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tests.glob('*.py')}
        driver = '''import {loadPyodide} from '/core/pyodide.mjs';
import {writeFileSync} from 'node:fs';
const py=await loadPyodide();
for(const [name,root] of [['packages','/packages'],['tests','/qualification/source/pyyaml-6.0.3/tests/legacy_tests']]) {
 py.FS.mkdir('/'+name);py.FS.mount(py.FS.filesystems.NODEFS,{root},'/'+name);
}
try {
const result=await py.runPythonAsync(`
import sys,os,json,io,contextlib,re
sys.path[:0]=['/packages','/tests']
os.chdir('/tests')
os.environ['YAML_TEST_FUNCTIONS']='test_c_scanner test_c_parser test_c_emitter'
import yaml,test_appliance,test_yaml_ext
captured=io.StringIO()
with contextlib.redirect_stdout(captured):
    ok=test_appliance.run(test_yaml_ext,args=[])
text=captured.getvalue()
json.dumps({'ok':bool(ok),'tests':next(int(line.split(':')[1]) for line in text.splitlines() if line.startswith('TESTS:')),
           'pyyaml':yaml.__version__,'native_extension':yaml._yaml.__file__,'output':text})
`);
const observed=JSON.parse(result);
writeFileSync('/qualification/upstream-output.txt',observed.output,{flag:'wx'});
delete observed.output;
writeFileSync('/qualification/observed.json',JSON.stringify(observed)+'\\n',{flag:'wx'});
console.log(observed);
if(!observed.ok)process.exitCode=1;
} catch(error) {
 writeFileSync('/qualification/error.txt',String(error.stack).slice(-12000),{flag:'wx'});
 console.error(error.message);process.exitCode=1;
}
'''
        (RUN / 'tests.mjs').write_text(driver)
        args = ['docker', 'run', '--name', NAME, '--network=none', '--cpus=1', '--memory=1g',
                '--memory-swap=1g', '--pids-limit=128', '--mount', f'type=bind,src={RUN},dst=/qualification',
                '--mount', f'type=bind,src={CORE},dst=/core,readonly',
                '--mount', f'type=bind,src={PACKAGES},dst=/packages,readonly', '--workdir', '/qualification',
                '--entrypoint', 'node', 'bifrost-test-api-dev:bifrost-test-7ddfac36', 'tests.mjs']
        report['command'] = args
        with (RUN / 'runner.log').open('x') as log:
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
