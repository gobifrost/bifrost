"""One bounded synthetic cloud-controller job in the assigned existing guest."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
RUN = ROOT / 'dev-deploy-2037ac0'
PRIVATE = RUN / 'cloud-private-request-signal-v1'
NAME = 'bifrost-pyodide-signal-cloud-v1'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def main():
    if subprocess.check_output(['hostname'], text=True).strip() != 'bifrost-pyodide-test-01':
        raise RuntimeError('Assigned guest required')
    if shutil.disk_usage(ROOT).free < 5 * 1024**3:
        raise RuntimeError('Owned guest free disk guard failed')
    image_id = subprocess.check_output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], text=True).strip()
    if image_id != 'sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b':
        raise RuntimeError('Pinned controller image changed')
    profile = json.loads((PRIVATE / 'profile.json').read_text())
    if profile['expires_at_ms'] <= time.time() * 1000:
        raise RuntimeError('Expired cloud controller profile')
    environment = json.loads(subprocess.check_output(['docker', 'inspect', 'bifrost-debug-074217d4-api-1',
                                                      '--format', '{{json .Config.Env}}'], text=True))
    envfile = PRIVATE / 'controller-api.env'
    with envfile.open('x') as output:
        os.chmod(envfile, 0o600)
        for value in environment:
            if '\r' in value or '\n' in value:
                raise RuntimeError('Multiline container environment refused')
            output.write(value + '\n')
    command = ['timeout', '180', 'docker', 'run', '--rm', '--name', NAME,
               '--network=bifrost-debug-074217d4_default', '--cpus=1', '--memory=768m', '--memory-swap=768m', '--pids-limit=128',
               '--env-file', str(envfile), '--volumes-from', 'bifrost-debug-074217d4-api-1:ro',
               '--mount', f'type=bind,src={ROOT}/dev-2037ac0/api/tests,dst=/app/tests,readonly',
               '--mount', f'type=bind,src={RUN}/request-signal/qualify.py,dst=/qualification/qualify.py,readonly',
               '--mount', f'type=bind,src={PRIVATE},dst=/qualification/private',
               '--entrypoint', 'python', IMAGE, '/qualification/qualify.py']
    started = time.monotonic()
    try:
        with (PRIVATE / 'controller.private.log').open('x') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=195)
        control = {'exit_code': result.returncode, 'guest': 'CT128', 'command': command,
                   'source_api': '2037ac0d9ec95e94a77ce440001ae016d4215407', 'image_id': image_id,
                   'elapsed_seconds': time.monotonic() - started}
        (PRIVATE / 'control.json').write_text(json.dumps(control, indent=2) + '\n')
        print(json.dumps(control))
        return result.returncode
    finally:
        envfile.unlink()
        running = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'name=^/' + NAME + '$'], text=True).strip()
        if running:
            subprocess.run(['docker', 'stop', '-t', '5', NAME], check=True, capture_output=True, timeout=15)


if __name__ == '__main__':
    raise SystemExit(main())
