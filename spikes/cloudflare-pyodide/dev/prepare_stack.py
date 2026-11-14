"""Deploy the owned dev stack in CT128 using upstream debug.sh and an explicit overlay."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import time

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
SOURCE = '2037ac0d9ec95e94a77ce440001ae016d4215407'
CHECKOUT = ROOT / 'dev-2037ac0'
RUN = ROOT / 'dev-deploy-2037ac0'
IMAGE = 'bifrost-test-api-dev:bifrost-test-7ddfac36'


def output(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=60, **kwargs).strip()


def main():
    if output(['hostname']) != 'bifrost-pyodide-test-01' or output(['docker', 'ps', '-q']):
        raise RuntimeError('Wrong guest or active containers: reconcile before deployment')
    if output(['git', 'rev-parse', 'HEAD'], cwd=CHECKOUT) != SOURCE or output(['git', 'status', '--porcelain'], cwd=CHECKOUT):
        raise RuntimeError('Source identity or cleanliness changed')
    changes = output(['git', 'diff', '--name-only', '0c3241789a15e418d02ca8eb6fbd8fac8016edef', SOURCE, '--', 'api', 'client'], cwd=CHECKOUT).splitlines()
    if any(not name.startswith('api/tests/') for name in changes):
        raise RuntimeError('Cached backend build inputs no longer match')
    if shutil.disk_usage(ROOT).used > 24 * 1024**3:
        raise RuntimeError('Disk guard')
    RUN.mkdir(mode=0o700)
    (RUN / 'home').mkdir()
    credentials = {'password': secrets.token_urlsafe(32), 'secret_key': secrets.token_urlsafe(48)}
    secret_file = RUN / 'dev-access.json'
    secret_file.write_text(json.dumps(credentials))
    secret_file.chmod(0o600)
    port_overlay = CHECKOUT / 'docker-compose.debug.port.yml'
    (RUN / 'original-port.yml').write_bytes(port_overlay.read_bytes())
    overlay = 'services:\n'
    for service, memory in [('init', '512m'), ('api', '1g'), ('worker', '1g'), ('scheduler', '512m'), ('scheduler-fixtures', '256m')]:
        overlay += f'  {service}:\n    image: {IMAGE}\n    build: !reset null\n    cpus: 1\n    mem_limit: {memory}\n'
        if service == 'api':
            overlay += '    environment:\n      BIFROST_DEFAULT_USER_PASSWORD: ${DEV_STACK_PASSWORD:?required}\n'
    overlay += '''  client:
    image: bifrost-pyodide-dev-client:2037ac0
    ports:
      - "${DEBUG_CLIENT_PORT}:80"
    cap_add: !reset []
    devices: !reset []
    cpus: 1
    mem_limit: 768m
'''
    port_overlay.write_text(overlay)
    env = dict(os.environ, HOME=str(RUN / 'home'), BIFROST_FORCE_PORT='1',
               DEV_STACK_PASSWORD=credentials['password'], BIFROST_SECRET_KEY=credentials['secret_key'],
               COMPOSE_PARALLEL_LIMIT='1', DOCKER_BUILDKIT='1')
    report = {'source': SOURCE, 'guest': 128, 'scope': 'isolated development stack',
              'overlay_sha256': hashlib.sha256(overlay.encode()).hexdigest(),
              'backend_image_id': output(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}']),
              'command': ['./debug.sh', 'up'], 'started_at_epoch': time.time(), 'deployment_passed': False}
    # Parse the merged config without emitting secret-bearing environment values.
    config = subprocess.run(['docker', 'compose', '-f', 'docker-compose.debug.yml', '-f', 'docker-compose.debug.port.yml', 'config', '--quiet'],
                            cwd=CHECKOUT, env={**env, 'DEBUG_CLIENT_PORT': '37991'}, capture_output=True, text=True)
    if config.returncode:
        raise RuntimeError('Compose overlay validation failed')
    started = time.monotonic()
    with (RUN / 'debug-up.private.log').open('x') as log:
        process = subprocess.Popen(['./debug.sh', 'up'], cwd=CHECKOUT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        while process.poll() is None:
            if time.monotonic() - started > 1200 or shutil.disk_usage(ROOT).used > 24 * 1024**3:
                os.killpg(process.pid, signal.SIGTERM)
                raise RuntimeError('Deployment wall/disk guard')
            time.sleep(1)
    report['exit_code'] = process.returncode
    report['elapsed_seconds'] = time.monotonic() - started
    if process.returncode == 0:
        report['deployment_passed'] = True
    (RUN / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    if process.returncode:
        raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
