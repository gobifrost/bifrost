#!/usr/bin/env python3
"""Self-test: the conformance suite must PASS a correct provider and FAIL a broken one.

This is the gate from issue #1400. A suite that cannot fail is worthless, so the
harness deliberately serves two providers:

  good    — one physical launch per incarnation, replay-safe, durable ledger
  broken  — re-launches on replay (the sequential duplicate launch failure)

Expected: the suite exits 0 against `good` and exits non-zero against `broken`,
with the duplicate-launch case among the failures.

Run: python selftest.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

from capacity_core import (  # noqa: E402
    CapacityAdapter,
    CapacityEngine,
    CapacityHandler,
    Context,
    Ledger,
    Observation,
    ProtocolError,
    ProviderConfig,
    resource_doc,
)

SCOPE = "conformance-selftest"
TOKEN = "selftest-token-0123456789abcdef0123456789abcdef"


class FakeAdapter(CapacityAdapter):
    """Correct adapter: at most one physical launch per incarnation."""

    def __init__(self) -> None:
        self.launches: dict[str, list[str]] = {}
        self.live: dict[str, bool] = {}
        self.pending_fault: str | None = None
        self.lock = threading.Lock()

    def _maybe_fault(self) -> None:
        if self.pending_fault == "start":
            self.pending_fault = None
            raise RuntimeError("injected start fault")

    def prepare(self, ctx: Context, spec: dict) -> dict:
        return {"started": False}

    def start(self, ctx: Context, handle: dict) -> Observation:
        self._maybe_fault()
        with self.lock:
            if ctx.worker_id not in self.launches:
                self.launches[ctx.worker_id] = [f"launch-{len(self.launches)}"]
            self.live[ctx.worker_id] = True
        return Observation("starting", {**handle, "started": True})

    def observe(self, ctx: Context, handle: dict) -> Observation:
        if not handle.get("started"):
            return Observation("prepared", handle)
        with self.lock:
            live = self.live.get(ctx.worker_id, False)
        return Observation("running" if live else "stopped", handle)

    def release(self, ctx: Context, handle: dict) -> Observation:
        with self.lock:
            self.live[ctx.worker_id] = False
        return Observation("deleted", handle)


class BrokenAdapter(FakeAdapter):
    """Re-launches on every start call — must be rejected by the suite."""

    def start(self, ctx: Context, handle: dict) -> Observation:
        with self.lock:
            self.launches.setdefault(ctx.worker_id, []).append(
                f"launch-{ctx.worker_id[:8]}-{len(self.launches[ctx.worker_id])}"
            )
            self.live[ctx.worker_id] = True
        return Observation("starting", {**handle, "started": True})


class BrokenEngine(CapacityEngine):
    """A provider that forgot the replay guard — must be rejected by the suite.

    This is the realistic failure: the author calls the cloud on every start
    request because they assume the controller never retries.
    """

    def start(self, worker_id: str, body: dict) -> dict:
        row = self.ledger.read(worker_id)
        if row is None:
            raise ProtocolError(404, "unknown worker")
        if body.get("resource_uid") != row["resource_uid"]:
            raise ProtocolError(409, "resource UID does not match the prepared incarnation")
        ctx = self._ctx(worker_id, row)
        obs = self.adapter.start(ctx, row.get("handle") or {})
        row["state"] = obs.state
        row["handle"] = obs.handle or row.get("handle") or {}
        self.ledger.write(worker_id, row)
        return resource_doc(self.cfg, worker_id, row, row["state"])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_admin(adapter: FakeAdapter, scope: str) -> ThreadingHTTPServer:
    class Admin(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # silence
            return

        def _json(self, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            query = dict(urllib.parse.parse_qsl(parsed.query))
            if parsed.path == "/observe":
                worker_id = query.get("worker_id", "")
                with adapter.lock:
                    self._json({"launches": list(adapter.launches.get(worker_id, [])),
                                "running": sum(1 for v in adapter.live.values() if v)})
                return
            if parsed.path == "/restart":
                # Simulated restart: adapter memory is lost, the ledger is not.
                with adapter.lock:
                    adapter.live.clear()
                self._json({"ok": True})
                return
            if parsed.path == "/stop":
                with adapter.lock:
                    adapter.live[query.get("worker_id", "")] = False
                self._json({"ok": True})
                return
            if parsed.path == "/fault":
                adapter.pending_fault = query.get("phase") or "start"
                self._json({"ok": True})
                return
            if parsed.path == "/clear-fault":
                adapter.pending_fault = None
                self._json({"ok": True})
                return
            self.send_response(404)
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", free_port()), Admin)


def serve_provider(
    adapter: FakeAdapter, store_path: str, engine_cls: type = CapacityEngine
) -> tuple[ThreadingHTTPServer, ProviderConfig]:
    cfg = ProviderConfig(
        token=TOKEN,
        scope=SCOPE,
        worker_version="selftest-build-1",
        memory_limit_bytes=2 * 1024**3,
        max_workers=4,
        max_lifetime_seconds=3600,
        port=free_port(),
        starting_grace_seconds=0,
    )
    ledger = Ledger(store_path)
    engine = engine_cls(cfg, adapter, ledger)
    server = ThreadingHTTPServer(("127.0.0.1", cfg.port), CapacityHandler)
    server.engine = engine  # type: ignore[attr-defined]
    server.cfg = cfg  # type: ignore[attr-defined]
    return server, cfg


def run_suite(base_url: str, control_url: str, label: str) -> int:
    env = dict(os.environ, CONTROL_URL=control_url)
    cmd = [
        sys.executable, str(HERE / "conformance.py"),
        "--base-url", base_url,
        "--token", TOKEN,
        "--scope", SCOPE,
        "--hook-command", f"{sys.executable} {HERE / 'hooks' / 'control_http.py'}",
    ]
    print(f"\n===== conformance against {label} =====", flush=True)
    proc = subprocess.run(" ".join(f'"{c}"' if " " in c else c for c in cmd),
                          shell=True, env=env, capture_output=True, text=True, timeout=600)
    print(proc.stdout, flush=True)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, flush=True)
    return proc.returncode


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        # -- good provider ------------------------------------------------
        good = FakeAdapter()
        provider, cfg = serve_provider(good, str(Path(tmp) / "good-ledger.json"))
        admin = build_admin(good, SCOPE)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        threading.Thread(target=admin.serve_forever, daemon=True).start()
        time.sleep(0.5)
        code = run_suite(
            f"http://127.0.0.1:{cfg.port}",
            f"http://127.0.0.1:{admin.server_address[1]}",
            "correct provider",
        )
        if code != 0:
            failures.append("correct provider was rejected")
        provider.shutdown()
        admin.shutdown()

        # -- broken provider ---------------------------------------------
        broken = BrokenAdapter()
        provider, cfg = serve_provider(
            broken, str(Path(tmp) / "broken-ledger.json"), engine_cls=BrokenEngine
        )
        admin = build_admin(broken, SCOPE)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        threading.Thread(target=admin.serve_forever, daemon=True).start()
        time.sleep(0.5)
        code = run_suite(
            f"http://127.0.0.1:{cfg.port}",
            f"http://127.0.0.1:{admin.server_address[1]}",
            "provider that double-launches",
        )
        if code == 0:
            failures.append("broken provider passed conformance")
        provider.shutdown()
        admin.shutdown()

    print("\n===== selftest result =====")
    if failures:
        for item in failures:
            print(f"FAIL: {item}")
        return 1
    print("PASS: suite accepts a correct provider and rejects a duplicate-launching one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
