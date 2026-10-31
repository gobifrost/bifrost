"""Fake external API for I/O-wait workflows (the PSA/RMM/Graph call shape).

Stdlib only; runs on the Bifrost image as ``python fake_api.py`` in ``bench-infra``.

- ``GET /io?ms=200&bytes=2000&tag=<bench_request_id>``: waits ``ms``, returns
  ``bytes`` of payload, and records a span for ``tag``;
- ``GET /io?gate=<name>&tag=...``: blocks until ``POST /gate/<name>`` releases it
  (the stale-worker drill holds a request in flight this way);
- ``GET /integration/sites`` and ``/integration/sites/<id>``: public synthetic
  interactive-fixture payloads with declared delay and payload size;
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
_SITE_COUNT = 88
_MAX_DELAY_MS = 30_000
_MAX_PAYLOAD_BYTES = 1_000_000


def _gate(name: str) -> threading.Event:
    with _LOCK:
        return _GATES.setdefault(name, threading.Event())


def _bounded_integer(query: dict[str, str], name: str, maximum: int) -> int:
    """Read explicit fixture controls without accepting negative or huge values."""
    try:
        value = int(query.get(name, "0"))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}")
    return value


def _integration_payload(path: str, payload_bytes: int) -> dict | None:
    """Return deterministic, non-customer portal-shaped data for the fixture."""
    padding = "x" * payload_bytes
    if path == "/integration/sites":
        return {
            "items": [{"id": index, "name": f"Synthetic Site {index:03d}"} for index in range(1, _SITE_COUNT + 1)],
            "payload_padding": padding,
        }
    prefix = "/integration/sites/"
    if path.startswith(prefix):
        try:
            site_id = int(path.removeprefix(prefix))
        except ValueError:
            return None
        if not 1 <= site_id <= _SITE_COUNT:
            return None
        return {
            "id": site_id,
            "name": f"Synthetic Site {site_id:03d}",
            "devices": [{"id": f"device-{site_id:03d}", "status": "managed"}],
            "payload_padding": padding,
        }
    return None


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
        if url.path == "/integration/sites" or url.path.startswith("/integration/sites/"):
            try:
                delay_ms = _bounded_integer(query, "delay_ms", _MAX_DELAY_MS)
                payload_bytes = _bounded_integer(query, "payload_bytes", _MAX_PAYLOAD_BYTES)
            except ValueError as error:
                self._send(400, json.dumps({"error": str(error)}).encode())
                return
            payload = _integration_payload(url.path, payload_bytes)
            if payload is None:
                self._send(404, b"{}")
                return
            start = time.time()
            time.sleep(delay_ms / 1000)
            body = json.dumps(payload, separators=(",", ":")).encode()
            finish = time.time()
            with _LOCK:
                _SPANS.append({
                    "tag": query.get("tag", ""), "kind": query.get("kind", ""), "route": url.path,
                    "arrival": arrival, "start": start, "finish": finish, "bytes": len(body),
                    "declared_delay_ms": delay_ms, "declared_payload_bytes": payload_bytes,
                })
            self._send(200, body)
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
