"""Offline HTTP transport parity with the canonical capacity controller."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conformance import Client


class ConformanceClientTests(unittest.TestCase):
    def serve(self, records: list[dict], redirect_target: str | None = None) -> str:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def respond(self) -> None:
                records.append({
                    "method": self.command,
                    "path": self.path,
                    "authenticated": bool(self.headers.get("Authorization")),
                })
                if self.path.startswith("/redirect/"):
                    self.send_response(int(self.path.rsplit("/", 1)[1]))
                    self.send_header("Location", redirect_target or "/target")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                raw = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = respond
            do_POST = respond

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()

        def cleanup() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "owned fixture server leaked")

        self.addCleanup(cleanup)
        return f"http://127.0.0.1:{server.server_port}"

    def test_redirect_status_is_preserved_without_reissuing_get_or_post(self) -> None:
        records: list[dict] = []
        client = Client(self.serve(records), "synthetic-capacity-token", "fixture-scope")
        for method in ("GET", "POST"):
            for code in (301, 302, 303, 307, 308):
                with self.subTest(method=method, status=code):
                    before = len(records)
                    status, _body = client.request(method, f"/redirect/{code}")
                    self.assertEqual(code, status)
                    self.assertEqual(before + 1, len(records))

    def test_cross_origin_target_never_receives_the_authenticated_request(self) -> None:
        target_requests: list[dict] = []
        target = self.serve(target_requests) + "/target"
        origin_requests: list[dict] = []
        client = Client(self.serve(origin_requests, target), "synthetic-capacity-token", "fixture-scope")
        for method, code in (("GET", 302), ("POST", 303)):
            with self.subTest(method=method, status=code):
                status, _body = client.request(method, f"/redirect/{code}")
                self.assertEqual(code, status)
                self.assertEqual([], target_requests)
                self.assertTrue(origin_requests[-1]["authenticated"])


if __name__ == "__main__":
    unittest.main()
