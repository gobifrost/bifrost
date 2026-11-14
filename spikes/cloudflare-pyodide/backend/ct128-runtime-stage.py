"""Pinned public dependency staging only; never invokes a Worker or cloud API."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = '785b560b9973020e1c13401383e1a73a87a54940'
CHECKOUT = ROOT / SOURCE
RUN = ROOT / 'runs/owner-run72-runtime-stage'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def output(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


class PackageRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(urllib.parse.urlsplit(newurl).hostname in {'files.pythonhosted.org', 'cdn.jsdelivr.net'},
                'Unapproved package redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    require(output(['hostname']) == 'bifrost-pyodide-test-01', 'Wrong guest')
    binding = json.loads((ROOT / 'owner-binding.json').read_text())
    require(binding['vmid'] == 128 and binding['owner_thread_id'] == '6e1d35f9-0847-466a-a570-f72fd16f9ed7', 'Wrong owner')
    require(not output(['docker', 'ps', '-q']), 'Reconcile running containers first')
    require(shutil.disk_usage(ROOT).free > 20 * 1024**3, 'Insufficient scratch')
    RUN.mkdir()
    (RUN / 'home').mkdir()
    (RUN / 'wheels').mkdir()
    report = {'source': SOURCE, 'guest': 128, 'worker_invocations': 0, 'cloud_invocations': 0,
              'started_at_epoch': time.time(), 'commands': [], 'wheels': [], 'staging_passed': False,
              'container': 'pyodide-ddf316c1-run72-stage', 'wall_limit_seconds': 900,
              'container_limits': {'cpus': 1.5, 'memory_mib': 3072, 'pids': 256},
              'guest_limits': {'cpus': 2, 'memory_mib': 4096, 'swap': 0}}
    started = time.monotonic()
    try:
        repo = ROOT / 'sources/bifrost-upstream.git'
        output(['git', '--git-dir', str(repo), 'fetch', 'https://github.com/MTG-Thomas/bifrost-upstream.git', SOURCE])
        output(['git', '--git-dir', str(repo), 'worktree', 'add', '--detach', str(CHECKOUT), SOURCE])
        require(not output(['git', 'status', '--porcelain'], cwd=CHECKOUT), 'Dirty source')
        spike = CHECKOUT / 'spikes/cloudflare-pyodide'
        report['input_sha256'] = {name: hashlib.sha256((spike / name).read_bytes()).hexdigest()
                                  for name in ('package-lock.json', 'pylock.toml', 'backend/runtime_guard.py')}
        args = ['docker', 'run', '--name', report['container'], '--cpus=1.5', '--memory=3g',
                '--memory-swap=3g', '--pids-limit=256', '--mount', f'type=bind,src={spike},dst=/spike',
                '--mount', f'type=bind,src={RUN / "home"},dst=/isolated-home',
                '--workdir', '/spike', '--env', 'HOME=/isolated-home', '--env', 'CI=true',
                '--entrypoint', 'npm', IMAGE, 'ci', '--ignore-scripts', '--no-audit', '--no-fund']
        report['commands'].append(args)
        with (RUN / 'npm-ci.log').open('x') as log:
            process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
            while process.poll() is None:
                require(time.monotonic() - started < 900 and shutil.disk_usage(ROOT).used < 24 * 1024**3,
                        'Wall/disk staging bound reached')
                time.sleep(1)
            require(process.returncode == 0, 'Pinned npm installation failed')
        opener = urllib.request.build_opener(PackageRedirects())
        packages = tomllib.loads((spike / 'pylock.toml').read_text())['packages']
        target = spike / 'python_modules'
        target.mkdir()
        for package in packages:
            require(time.monotonic() - started < 900, 'Staging deadline reached')
            wheel = package['wheels'][0]
            url = wheel['url']
            require(urllib.parse.urlsplit(url).hostname in {'files.pythonhosted.org', 'cdn.jsdelivr.net'}, 'Unapproved wheel source')
            with opener.open(url, timeout=45) as response:
                data = response.read(20 * 1024**2 + 1)
            require(len(data) <= 20 * 1024**2, 'Oversized wheel')
            digest = hashlib.sha256(data).hexdigest()
            require(digest == wheel['hashes']['sha256'], 'Wheel hash mismatch')
            path = RUN / 'wheels' / url.rsplit('/', 1)[1]
            path.write_bytes(data)
            with zipfile.ZipFile(path) as archive:
                require(sum(i.file_size for i in archive.infolist()) < 50 * 1024**2, 'Oversized expanded wheel')
                for member in archive.infolist():
                    relative = PurePosixPath(member.filename)
                    require(not relative.is_absolute() and '..' not in relative.parts and '\\' not in member.filename,
                            'Unsafe wheel path')
                    require((member.external_attr >> 16) & 0o170000 != 0o120000, 'Wheel symlink refused')
                archive.extractall(target)
            report['wheels'].append({'name': package['name'], 'version': package['version'], 'url': url, 'sha256': digest})
        for args in (['python3', str(spike / 'backend/prepare-runtime.py'), '--manifest', 'ct128-runtime-source.json'],
                     ['python3', str(spike / 'backend/pack-engine.py'), '--current-runtime', '--manifest', 'ct128-runtime-manifest.json']):
            report['commands'].append(args)
            report.setdefault('command_output', []).append(output(args))
        report['manifest'] = json.loads((spike / 'results/ct128-runtime-manifest.json').read_text())
        report['staging_passed'] = True
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        state = subprocess.run(['docker', 'inspect', report['container']], capture_output=True, text=True)
        if state.returncode == 0:
            item = json.loads(state.stdout)[0]
            report['container_state'] = item['State']
            if item['State']['Running']:
                subprocess.run(['docker', 'stop', '-t', '5', report['container']], check=True)
        report['elapsed_seconds'] = time.monotonic() - started
        report['finished_at_epoch'] = time.time()
        (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({k: v for k, v in report.items() if k != 'manifest'}))


if __name__ == '__main__':
    main()
