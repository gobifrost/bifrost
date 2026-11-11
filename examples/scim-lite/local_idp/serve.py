"""Fake local IdP delta endpoint for pull-sync testing (stdlib only).

Serves the deterministic 300-user directory:
  GET /users?cursor=<offset>&limit=<n>  -> {"users": [...], "next_cursor": str|None}
  GET /healthz -> {"ok": true}

Usage: python3 local_idp/serve.py [port]
"""

from __future__ import annotations

import json
import random
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

GROUPS = ["support-tier1", "support-leads", "eng", "sales", "unmapped-xyz"]
NAMES = ["Ada", "Bob", "Cara", "Dan", "Eli", "Fay", "Gus", "Hal", "Ivy", "Jay", "Kim", "Lou"]


def build_directory(n: int = 300, seed: int = 7, domain: str | None = None) -> list[dict]:
    rng = random.Random(seed)
    users = []
    for i in range(n):
        ext = f"bulk-{i:03d}"
        email = f"bulkuser{i:03d}@example.com"
        if domain:
            ext = f"{domain.split('.')[0]}-{ext}"
            email = f"bulkuser{i:03d}@{domain}"
        users.append(
            {
                "external_id": ext,
                "email": email,
                "name": f"{NAMES[i % len(NAMES)]} Bulk{i:03d}",
                "active": rng.random() > 0.1,
                "groups": sorted(rng.sample(GROUPS, rng.randint(0, 3))),
            }
        )
    return users


DIRECTORY = build_directory()
DIRECTORIES = {"example.com": DIRECTORY}


def directory_for(domain: str | None) -> list[dict]:
    if not domain or domain == "example.com":
        return DIRECTORY
    if domain not in DIRECTORIES:
        DIRECTORIES[domain] = build_directory(domain=domain)
    return DIRECTORIES[domain]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            return self._send({"ok": True})
        if parsed.path != "/users":
            return self._send({"error": "not found"}, 404)
        qs = parse_qs(parsed.query)
        try:
            offset = int(qs.get("cursor", ["0"])[0] or 0)
            limit = max(1, min(int(qs.get("limit", ["100"])[0] or 100), 200))
        except ValueError:
            return self._send({"error": "bad cursor/limit"}, 400)
        directory = directory_for((qs.get("domain") or [None])[0])
        page = directory[offset : offset + limit]
        nxt = str(offset + len(page)) if offset + len(page) < len(directory) else None
        self._send({"users": page, "next_cursor": nxt})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8471
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
