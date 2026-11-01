"""Independent package tests in official Pyodide, never a Cloudflare runtime substitute."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
RUN = ROOT / 'runs/pyodide-package-qualification-resume'
URL = 'https://github.com/pyodide/pyodide/releases/download/314.0.6/pyodide-core-314.0.6.tar.bz2'
SHA256 = '1016c31e39ce3764d9a418cbb491a392c802c1b86ccc1367f009f5c59bf8f5fd'
NAME = 'pyodide-ddf316c1-package-qualification'
PACKAGES = ROOT / '785b560b9973020e1c13401383e1a73a87a54940/spikes/cloudflare-pyodide/python_modules'


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname not in {'github.com', 'release-assets.githubusercontent.com'}:
            raise RuntimeError('Unapproved publisher redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Wrong guest')
    if subprocess.check_output(['docker', 'ps', '-q'], text=True).strip():
        raise RuntimeError('Reconcile active containers first')
    RUN.mkdir()
    report = {'source': '688e7a0f4f25aa8a280838da2a7d185740d2aa72', 'url': URL,
              'expected_sha256': SHA256, 'runtime': 'official-pyodide-on-node',
              'cloudflare_runtime_qualified': False, 'physical_termination_verified': False,
              'cloud_invocations': 0, 'started_at_epoch': time.time()}
    try:
        with urllib.request.build_opener(Redirects()).open(URL, timeout=60) as response:
            data = response.read(16 * 1024**2 + 1)
        if len(data) > 16 * 1024**2 or hashlib.sha256(data).hexdigest() != SHA256:
            raise RuntimeError('Official core archive integrity mismatch')
        archive = RUN / 'pyodide-core.tar.bz2'
        archive.write_bytes(data)
        with tarfile.open(archive) as tar:
            for item in tar.getmembers():
                if not (item.isfile() or item.isdir()) or Path(item.name).is_absolute() or '..' in Path(item.name).parts:
                    raise RuntimeError('Unsafe runtime archive member')
            tar.extractall(RUN / 'core', filter='data')
        modules = list((RUN / 'core').rglob('pyodide.mjs'))
        if len(modules) != 1:
            raise RuntimeError('Ambiguous official loader')
        relative = modules[0].relative_to(RUN).as_posix()
        driver = f'''import {{loadPyodide}} from './{relative}';
import {{writeFileSync}} from 'node:fs';
const py=await loadPyodide();
py.FS.mkdir('/packages');
py.FS.mount(py.FS.filesystems.NODEFS,{{root:'/packages'}},'/packages');
const observed=await py.runPythonAsync(`
import sys,sysconfig,json
sys.path.insert(0, '/packages')
import pyodide,pydantic,pydantic_core,yaml
from pydantic_core import SchemaValidator,core_schema,ValidationError
checks={{}}
checks['int_coercion']=SchemaValidator(core_schema.int_schema()).validate_python('42') == 42
try:
    SchemaValidator(core_schema.int_schema(strict=True)).validate_python('42')
except ValidationError:
    checks['strict_int_rejected']=True
else:
    checks['strict_int_rejected']=False
checks['native_yaml']=yaml.CSafeLoader is not None and yaml.load('answer: 42',Loader=yaml.CSafeLoader)=={{'answer':42}}
class Values(pydantic.BaseModel):
    values:list[int]
checks['pydantic_model']=Values.model_validate({{'values':['1',2]}}).model_dump()=={{'values':[1,2]}}
assert all(checks.values()),checks
json.dumps({{'pyodide':pyodide.__version__,'python':list(sys.version_info[:3]),
 'cache_tag':sys.implementation.cache_tag,'soabi':sysconfig.get_config_var('SOABI'),
 'platform':sysconfig.get_platform(),'pydantic':pydantic.__version__,
 'pydantic_core':pydantic_core.__version__,'pyyaml':yaml.__version__,'checks':checks}})
`);
writeFileSync('/qualification/observed.json',observed+'\\n',{{flag:'wx'}});
console.log(observed);
'''
        (RUN / 'qualify.mjs').write_text(driver)
        args = ['docker', 'run', '--name', NAME, '--network=none', '--cpus=1',
                '--memory=1g', '--memory-swap=1g', '--pids-limit=128',
                '--mount', f'type=bind,src={RUN},dst=/qualification',
                '--mount', f'type=bind,src={PACKAGES},dst=/packages,readonly',
                '--workdir', '/qualification', '--entrypoint', 'node',
                'bifrost-test-api-dev:bifrost-test-7ddfac36', 'qualify.mjs']
        report['command'] = args
        with (RUN / 'qualification.log').open('x') as log:
            outcome = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=120)
        report['exit_code'] = outcome.returncode
        if (RUN / 'observed.json').exists():
            report['observed'] = json.loads((RUN / 'observed.json').read_text())
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        if hasattr(exc, 'code'):
            report['http_status'] = exc.code
        raise
    finally:
        subprocess.run(['docker', 'stop', '-t', '3', NAME], capture_output=True)
        report['finished_at_epoch'] = time.time()
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
