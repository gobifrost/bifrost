#!/usr/bin/env python3
"""Prove complete conformance passes and broken/incomplete evidence cannot.

Exercise normal and uncertain-only duplicate-launch negatives, missing hooks,
and unsupported hooks. All providers are disposable loopback SDK fakes; this
checks the gate, not real-cloud runtime qualification.
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

    def _record_launch(self, ctx: Context) -> None:
        self.launches.setdefault(ctx.worker_id, [f"launch-{len(self.launches)}"])
        self.live[ctx.worker_id] = True

    def start(self, ctx: Context, handle: dict) -> Observation:
        self._maybe_fault()
        with self.lock:
            self._record_launch(ctx)
        if self.pending_fault == "start-response":
            self.pending_fault = None
            raise RuntimeError("accepted start response lost")
        return Observation("starting", {**handle, "started": True})

    def observe(self, ctx: Context, handle: dict) -> Observation:
        if not handle.get("started") and not self.launches.get(ctx.worker_id):
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

    def _record_launch(self, ctx: Context) -> None:
        launches = self.launches.setdefault(ctx.worker_id, [])
        launches.append(f"launch-{ctx.worker_id[:8]}-{len(launches)}")
        self.live[ctx.worker_id] = True


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



class UncertainReplayEngine(CapacityEngine):
    """Negative control: ordinary replay is safe, uncertain replay double-starts."""

    def start(self, worker_id: str, body: dict) -> dict:
        row = self.ledger.read(worker_id)
        if row is not None and row.get("intent") == "start":
            row["intent"] = None
            self.ledger.write(worker_id, row)
        return super().start(worker_id, body)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_admin(adapter: FakeAdapter, scope: str, provider: ThreadingHTTPServer) -> ThreadingHTTPServer:
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
                # Rebuild control state from its ledger, preserving physical
                # launches. This is offline restart proof, not cloud hosting.
                engine = provider.engine
                provider.engine = type(engine)(engine.cfg, adapter, Ledger(engine.ledger.path))
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


def run_suite(base_url: str, control_url: str, label: str, output: Path,
              hook_command: str | None) -> tuple[int, dict]:
    env = dict(os.environ, CONTROL_URL=control_url)
    cmd = [
        sys.executable, str(HERE / "conformance.py"),
        "--base-url", base_url, "--token", TOKEN, "--scope", SCOPE,
        "--json-out", str(output),
    ]
    if hook_command is not None:
        cmd.extend(["--hook-command", hook_command])
    print(f"\n===== conformance against {label} =====", flush=True)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    print(proc.stdout, flush=True)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr, flush=True)
    return proc.returncode, json.loads(output.read_text())


def main() -> int:
    failures: list[str] = []
    uncertainty = "uncertainty: lost start reply survives restart without duplicate or resurrection"
    mandatory = {
        uncertainty, "start: exactly one physical launch",
        "start: replay does not launch a second incarnation",
        "durability: prepared reservation survives provider restart",
        "durability: tombstone survives provider restart",
        "durability: stopped incarnation never restarts",
    }
    variants = [
        ("correct", FakeAdapter, CapacityEngine, "supported"),
        ("duplicate-launch", BrokenAdapter, BrokenEngine, "supported"),
        ("uncertain-duplicate", BrokenAdapter, UncertainReplayEngine, "supported"),
        ("no-hook", FakeAdapter, CapacityEngine, "none"),
        ("unsupported-hook", FakeAdapter, CapacityEngine, "unsupported"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        unsupported = Path(tmp) / "unsupported.py"
        unsupported.write_text("raise SystemExit(3)\n")
        for label, adapter_type, engine_type, hook_mode in variants:
            adapter = adapter_type()
            provider, cfg = serve_provider(adapter, str(Path(tmp) / f"{label}-ledger.json"), engine_type)
            admin = build_admin(adapter, SCOPE, provider)
            threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (provider, admin)]
            for thread in threads:
                thread.start()
            hook = (f"{sys.executable} {HERE / 'hooks' / 'control_http.py'}"
                    if hook_mode == "supported" else
                    f"{sys.executable} {unsupported}" if hook_mode == "unsupported" else None)
            try:
                code, report = run_suite(f"http://127.0.0.1:{cfg.port}",
                    f"http://127.0.0.1:{admin.server_address[1]}", label,
                    Path(tmp) / f"{label}.json", hook)
                cases = {case["name"]: case for case in report["cases"]}
                if not mandatory.issubset(cases):
                    failures.append(f"{label}: required cases omitted")
                if label == "correct":
                    if code != 0 or report["skipped"] or report["failed"] or report["passed"] != len(cases):
                        failures.append("correct provider was not completely qualified")
                elif label in {"duplicate-launch", "uncertain-duplicate"}:
                    target = uncertainty if label == "uncertain-duplicate" else "start: replay does not launch a second incarnation"
                    if code != 1 or cases.get(target, {}).get("status") != "FAIL":
                        failures.append(f"{label}: broken lifecycle was not rejected by its case")
                elif code != 2 or not report["skipped"]:
                    failures.append(f"{label}: missing evidence incorrectly qualified")
            finally:
                for server in (provider, admin):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join()
    print("\n===== selftest result =====")
    if failures:
        for item in failures:
            print(f"FAIL: {item}")
        return 1
    print("PASS: full evidence accepted; duplicate starts rejected; missing hooks incomplete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
