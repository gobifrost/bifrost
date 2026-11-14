"""Fetch one integrity-pinned official workerd dependency; no Worker invocation."""
import base64
import hashlib
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

ROOT = Path('/opt/bifrost-tests/pyodide-ddf316c1')
NAME = 'pyodide_314.0.6_2026-08-17_6.capnp.bin'
URL = 'https://pyodide-capnp-bin.edgeworker.net/' + NAME
INTEGRITY = 'PD/VpBeSMOIeAY4osOc158Sr2P6OT/OdL9a7LqOi4mA='


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname != 'pyodide-capnp-bin.edgeworker.net':
            raise RuntimeError('Dependency redirect outside official asset host refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main():
    run = ROOT / 'runs/owner-run72-runtime-cache'
    run.mkdir()
    receipt = {'url': URL, 'source_repository': 'cloudflare/workerd',
               'source_commit': '7caf6dee5bfe9af1b7710ddf66222900007af080',
               'metadata_path': 'build/python_metadata.bzl', 'cloud_invocations': 0,
               'r2_requests': 0, 'runtime_invocations': 0, 'started_at_epoch': time.time()}
    try:
        with urllib.request.build_opener(Redirects()).open(URL, timeout=60) as response:
            data = response.read(128 * 1024**2 + 1)
        if len(data) > 128 * 1024**2:
            raise RuntimeError('Runtime dependency exceeds bound')
        digest = hashlib.sha256(data).digest()
        if digest != base64.b64decode(INTEGRITY):
            raise RuntimeError('Official runtime integrity mismatch')
        with (run / NAME).open('xb') as output:
            output.write(data)
        receipt.update(verified=True, size_bytes=len(data), sha256=digest.hex())
    except Exception as error:
        receipt.update(verified=False, error_type=type(error).__name__)
        raise
    finally:
        (run / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt))


if __name__ == '__main__':
    main()
