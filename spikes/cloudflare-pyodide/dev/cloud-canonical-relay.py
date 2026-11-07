"""Bounded synthetic qualification relay; no durable job ownership or retries."""
from concurrent.futures import ThreadPoolExecutor
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import httpx

MAX_BODY = 262144
MAX_RESPONSE = 4194304
PROFILE = Path('/private/profile.json')


def sdk_path(path):
    path = path.split('?', 1)[0]
    return path.startswith('/api/sdk/') or path == '/api/tables' or path.startswith('/api/tables/') or path == '/auth/refresh'


def main():
    profile = json.loads(PROFILE.read_text())
    if (profile['endpoint'] != 'https://bifrost-pyodide-spike-ddf316c1.midtown-technology-group.workers.dev'
        or not time.time()*1000 < profile['expires_at_ms'] <= (time.time()+1800)*1000
        or len(profile['secret']) < 32 or profile['max_child_requests'] != 3):
        raise RuntimeError('Fixed bounded profile required')
    lock = threading.Lock()
    child_requests = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # No credential-bearing HTTP diagnostics.

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def dispatch(self):
            nonlocal child_requests
            origin = self.server.server_port == 8800
            if time.time()*1000 >= profile['expires_at_ms']:
                return self.finish_response(410)
            if origin and (not hmac.compare_digest(self.headers.get('X-Qualification-Auth', ''), profile['secret']) or not sdk_path(self.path)):
                return self.finish_response(403)
            if not origin and (self.command != 'POST' or self.path not in ('/', '/__fixture_warmup')):
                return self.finish_response(405)
            try:
                response_started = False
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= MAX_BODY or self.headers.get('Transfer-Encoding'):
                    return self.finish_response(413)
                body = self.rfile.read(size)
                if len(body) != size:
                    return self.finish_response(400)
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() not in ('host', 'connection', 'content-length', 'x-qualification-auth', 'x-dev-auth')}
                if origin:
                    target = 'http://api:8000'+self.path
                else:
                    with lock:
                        if child_requests >= 3:
                            return self.finish_response(429)
                        child_requests += 1
                        request_index = child_requests
                        Path('/private/relay-receipt.json').write_text(json.dumps({'child_requests': child_requests, 'expired': False}))
                    headers['X-Dev-Auth'] = profile['secret']
                    target = profile['endpoint']+self.path
                with httpx.Client(timeout=90, follow_redirects=False, trust_env=False) as client:
                    with client.stream(self.command, target, headers=headers, content=body) as response:
                        if not origin:
                            retained = bytearray()
                            self.send_response(response.status_code)
                            self.send_header('Content-Type', response.headers.get('Content-Type', 'application/octet-stream'))
                            self.end_headers()
                            response_started = True
                            total = 0
                            for chunk in response.iter_bytes():
                                total += len(chunk)
                                if total > MAX_RESPONSE:
                                    return  # Lost EOF remains uncertain; never invent a result.
                                retained.extend(chunk)
                                self.wfile.write(chunk)
                                self.wfile.flush()
                            # Private bounded inert evidence, never executed or published raw.
                            Path(f'/private/response-{request_index}.bin').write_bytes(retained)
                            Path(f'/private/response-{request_index}.json').write_text(json.dumps({
                                'status': response.status_code, 'bytes': total, 'path': self.path}))
                            return
                        chunks, total = [], 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > MAX_RESPONSE:
                                raise ValueError('Response retention bound')
                            chunks.append(chunk)
                        self.finish_response(response.status_code, b''.join(chunks), response.headers.get('Content-Type', 'application/octet-stream'))
            except Exception:
                if not response_started:
                    self.finish_response(502)

        def finish_response(self, status, body=b'', content_type='text/plain'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    servers = [ThreadingHTTPServer(('0.0.0.0', port), Handler) for port in (8799, 8800)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for server in servers:
            pool.submit(server.serve_forever)
        try:
            time.sleep(max(0, profile['expires_at_ms']/1000-time.time()))
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            Path('/private/relay-receipt.json').write_text(json.dumps({'child_requests': child_requests, 'expired': True}))


if __name__ == '__main__':
    main()
