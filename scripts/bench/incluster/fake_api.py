"""Fake external API for I/O-wait workflows (the PSA/RMM/Graph call shape).

Stdlib only; runs on the Bifrost image as ``python fake_api.py`` in ``bench-infra``.

- ``GET /io?ms=200&bytes=2000&tag=<bench_request_id>``: waits ``ms``, returns
  ``bytes`` of payload, and records a span for ``tag``;
- ``GET /io?gate=<name>&tag=...``: blocks until ``POST /gate/<name>`` releases it
  (the stale-worker drill holds a request in flight this way);
- ``GET /spans?since=<epoch>``: recorded spans (arrival, service start, finish);
- ``POST /spans/reset``: clears them.

Queue time is service start minus arrival (handler thread start); with a
thread per request it measures this server's own contention.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_LOCK = threading.Lock()
_SPANS: list[dict] = []
_GATES: dict[str, threading.Event] = {}


def _gate(name: str) -> threading.Event:
    with _LOCK:
        return _GATES.setdefault(name, threading.Event())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        """Silence per-request logging."""

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        arrival = time.time()
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        if url.path == "/spans":
            since = float(query.get("since", "0"))
            with _LOCK:
                spans = [span for span in _SPANS if span["arrival"] >= since]
            self._send(200, json.dumps({"spans": spans}).encode())
            return
        if url.path == "/healthz":
            self._send(200, b"{}")
            return
        if url.path != "/io":
            self._send(404, b"{}")
            return
        start = time.time()
        if "gate" in query:
            _gate(query["gate"]).wait()
        else:
            time.sleep(int(query.get("ms", "0")) / 1000)
        payload = b"x" * int(query.get("bytes", "0"))
        finish = time.time()
        with _LOCK:
            _SPANS.append({"tag": query.get("tag", ""), "arrival": arrival, "start": start, "finish": finish, "bytes": len(payload)})
        self._send(200, payload, "application/octet-stream")

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        path = urlparse(self.path).path
        if path == "/spans/reset":
            with _LOCK:
                _SPANS.clear()
            self._send(200, b"{}")
        elif path.startswith("/gate/"):
            _gate(path.removeprefix("/gate/")).set()
            self._send(200, b"{}")
        else:
            self._send(404, b"{}")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
