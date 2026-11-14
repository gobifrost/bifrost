"""Replace the owned failed Vite service with upstream nginx and compiled assets."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
CHECKOUT = ROOT / 'dev-2037ac0'
RUN = ROOT / 'dev-deploy-2037ac0'
IMAGE = 'nginx:alpine@sha256:df221db836e1754089190208cee7eeda94f233197056426eda74a43ab1abeac2'


def main():
    if not (RUN / 'frontend-dist/index.html').is_file():
        raise RuntimeError('Compiled upstream frontend missing')
    state = subprocess.check_output(['docker', 'inspect', 'bifrost-debug-074217d4-client-1', '--format', '{{json .State}}'], text=True)
    (RUN / 'initial-vite-state.json').write_text(state)
    config = (CHECKOUT / 'client/nginx.conf').read_text().replace('    # Gzip compression',
        '    location ^~ /health { proxy_pass http://api:8000; }\n    # Gzip compression', 1)
    nginx = RUN / 'nginx.conf'
    nginx.write_text(config)
    overlay = CHECKOUT / 'docker-compose.debug.port.yml'
    prefix = overlay.read_text().split('  client:\n', 1)[0]
    prefix += f'''  client:
    image: {IMAGE}
    build: !reset null
    command: [nginx, -g, "daemon off;"]
    ports:
      - "${{DEBUG_CLIENT_PORT}}:80"
    volumes: !override
      - {RUN / 'frontend-dist'}:/usr/share/nginx/html:ro
      - {nginx}:/etc/nginx/conf.d/default.conf:ro
    cap_add: !reset []
    devices: !reset []
    cpus: 0.5
    mem_limit: 128m
'''
    overlay.write_text(prefix)
    secrets = json.loads((RUN / 'dev-access.json').read_text())
    env = dict(os.environ, HOME=str(RUN / 'home'), DEV_STACK_PASSWORD=secrets['password'],
               BIFROST_SECRET_KEY=secrets['secret_key'], DEBUG_CLIENT_PORT='32767')
    command = ['docker', 'compose', '-p', 'bifrost-debug-074217d4', '-f', 'docker-compose.debug.yml',
               '-f', 'docker-compose.debug.port.yml', 'up', '-d', '--no-deps', '--no-build', 'client']
    subprocess.run(command, cwd=CHECKOUT, env=env, check=True, timeout=180)
    print('Compiled client deployed on the existing dev origin port 32767; credentials unchanged.')


if __name__ == '__main__':
    main()
