"""Shared external-worker capacity protocol core.

One implementation of the provider contract
(docs/architecture/external-worker-capacity.md), so every Python provider —
Floci proofs and real clouds — shares one lifecycle, one ledger, and one set of
validation rules.

Adapters implement four operations and never own protocol decisions:

    prepare(ctx, spec) -> handle      # MUST NOT start a process
    start(ctx, handle) -> Observation  # ensure at most ONE launcher
    observe(ctx, handle) -> Observation
    release(ctx, handle) -> Observation

The core owns authentication, scope checking, routing, reservation validation,
resource UIDs, lifecycle transitions, capacity accounting, tombstones, and
response serialization. Adapters own cloud calls, handles, physical state
interpretation, and out-of-memory evidence.

Ledger transactions contain ledger operations only. No cloud call is ever made
while holding the ledger lock.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from uuid import UUID

SCOPE_HEADER = "X-Bifrost-Capacity-Scope"

STATE_PREPARED = "prepared"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"
STATE_DELETED = "deleted"
STATE_FAILED = "failed"

LIVE_STATES = (STATE_PREPARED, STATE_STARTING, STATE_RUNNING)


def new_resource_uid() -> str:
    """Opaque, pattern-safe UID: `^[a-zA-Z0-9._:-]+$` excludes cloud paths.

    Cloud identifiers (ECS task ARNs, ACI/Cloud Run resource names) contain "/"
    and therefore cannot be the resource UID. They belong in the adapter handle.
    """
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Observation:
    """What an adapter can say about one incarnation.

    `absent` means the provider is certain nothing exists. It is distinct from
    an error: an adapter that cannot reach its cloud must raise, never report
    absence, or the core would fabricate a stopped incarnation.
    """

    state: str
    handle: dict[str, Any] = field(default_factory=dict)
    out_of_memory: bool = False


class CapacityAdapter(Protocol):
    def prepare(self, ctx: "Context", spec: dict[str, Any]) -> dict[str, Any]:
        """Create the resource, or return a ledger-only handle when the cloud cannot create without starting."""

    def start(self, ctx: "Context", handle: dict[str, Any]) -> Observation:
        """Start the prepared resource and report what was observed."""

    def observe(self, ctx: "Context", handle: dict[str, Any]) -> Observation:
        """Read the resource's current state without changing it."""

    def release(self, ctx: "Context", handle: dict[str, Any]) -> Observation:
        """Delete the resource and confirm it is gone."""


@dataclass(frozen=True)
class Context:
    scope: str
    worker_id: str
    resource_uid: str
    operation_id: str


class AdapterError(RuntimeError):
    """Adapter could not reach a decision. Never treated as absence."""


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


class Ledger:
    """Durable record store with retained tombstones.

    Single-process safety: an exclusive in-process lock plus atomic replacement.
    A multi-process deployment must supply a linearizable store instead; see
    docs/architecture/external-worker-capacity.md.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.rows: dict[str, dict[str, Any]] = {}
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                self.rows = json.load(fh).get("workers", {})

    def _flush(self) -> None:
        if not self.path:
            return
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"workers": self.rows}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def read(self, worker_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.rows.get(worker_id)
            return json.loads(json.dumps(row)) if row else None

    def write(self, worker_id: str, row: dict[str, Any]) -> None:
        with self.lock:
            row["revision"] = int(row.get("revision", 0)) + 1
            self.rows[worker_id] = row
            self._flush()

    def count_live(self, scope: str) -> int:
        with self.lock:
            return sum(
                1
                for row in self.rows.values()
                if row.get("scope") == scope and row.get("state") in LIVE_STATES
            )


# --------------------------------------------------------------------------
# Configuration and profiles
# --------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    token: str
    scope: str
    worker_version: str
    memory_limit_bytes: int
    max_workers: int
    max_lifetime_seconds: int
    port: int = 8443
    starting_grace_seconds: int = 30
    tls_cert_file: str | None = None
    tls_key_file: str | None = None

    @classmethod
    def from_env(cls, **extra: Any) -> "ProviderConfig":
        token = os.environ.get("CAPACITY_TOKEN", "")
        if not (32 <= len(token) <= 512 and token.isascii() and not any(c.isspace() for c in token)):
            raise SystemExit("CAPACITY_TOKEN must be 32-512 non-space ASCII chars")
        return cls(
            token=token,
            scope=os.environ.get("CAPACITY_SCOPE", "capacity"),
            worker_version=_require_env("WORKER_VERSION"),
            memory_limit_bytes=_env_int("MEMORY_LIMIT_BYTES", 4 * 1024**3),
            max_workers=_env_int("MAX_WORKERS", 2),
            max_lifetime_seconds=_env_int("MAX_LIFETIME_SECONDS", 6 * 3600),
            port=_env_int("PORT", 8443),
            starting_grace_seconds=_env_int("STARTING_GRACE_SECONDS", 30),
            tls_cert_file=os.environ.get("TLS_CERT_FILE"),
            tls_key_file=os.environ.get("TLS_KEY_FILE"),
            **extra,
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def profile_doc(cfg: ProviderConfig) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "scope": cfg.scope,
        "worker_version": cfg.worker_version,
        "memory_limit_bytes": cfg.memory_limit_bytes,
        "max_workers": cfg.max_workers,
        "max_lifetime_seconds": cfg.max_lifetime_seconds,
    }


def resource_doc(cfg: ProviderConfig, worker_id: str, row: dict[str, Any], state: str, oom: bool = False) -> dict[str, Any]:
    return {
        "scope": cfg.scope,
        "worker_id": worker_id,
        "worker_version": row["reservation"]["worker_version"],
        "resource_uid": row["resource_uid"],
        "state": state,
        "out_of_memory": oom,
    }


_RESERVATION_FIELDS = (
    "worker_id",
    "worker_version",
    "memory_limit_bytes",
    "max_lifetime_seconds",
    "drain_grace_seconds",
)


class ProtocolError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# Protocol engine (no HTTP)
# --------------------------------------------------------------------------


class CapacityEngine:
    """Protocol semantics, independent of transport. Unit-testable directly."""

    def __init__(self, cfg: ProviderConfig, adapter: CapacityAdapter, ledger: Ledger) -> None:
        self.cfg = cfg
        self.adapter = adapter
        self.ledger = ledger

    # -- helpers ---------------------------------------------------------

    def _ctx(self, worker_id: str, row: dict[str, Any]) -> Context:
        return Context(
            scope=self.cfg.scope,
            worker_id=worker_id,
            resource_uid=row["resource_uid"],
            operation_id=row.get("operation_id") or uuid.uuid4().hex,
        )

    def _settle(self, worker_id: str, row: dict[str, Any], obs: Observation) -> dict[str, Any]:
        """Persist an adapter observation. Ledger only — never re-calls the adapter."""
        state = obs.state
        if state == STATE_RUNNING and row["state"] == STATE_STARTING:
            elapsed = time.time() - (row.get("started_at") or 0)
            if elapsed < self.cfg.starting_grace_seconds:
                state = STATE_STARTING
        if state in (STATE_STOPPED, STATE_FAILED) and row["state"] in (STATE_STARTING, STATE_RUNNING):
            # A stopped incarnation is terminal: it may never be started again.
            state = STATE_STOPPED
            row["stopped_at"] = row.get("stopped_at") or time.time()
        row["state"] = state
        row["handle"] = obs.handle or row.get("handle") or {}
        if obs.out_of_memory:
            row["out_of_memory"] = True
        row["intent"] = None
        self.ledger.write(worker_id, row)
        return row

    def _project(self, worker_id: str, row: dict[str, Any]) -> dict[str, Any]:
        if row["state"] == STATE_DELETED:
            return resource_doc(self.cfg, worker_id, row, STATE_DELETED, bool(row.get("out_of_memory")))
        if row["state"] in (STATE_PREPARED, STATE_STOPPED):
            return resource_doc(self.cfg, worker_id, row, row["state"], bool(row.get("out_of_memory")))
        ctx = self._ctx(worker_id, row)
        try:
            obs = self.adapter.observe(ctx, row.get("handle") or {})
        except Exception:
            # Unreachable is not absent. Keep the last known state.
            return resource_doc(self.cfg, worker_id, row, row["state"], bool(row.get("out_of_memory")))
        if obs.state != row["state"]:
            # Observation is authoritative: a terminal transition must be
            # persisted here, or the no-restart fence is never armed and a
            # stopped incarnation would accept a fresh start.
            row = self._settle(worker_id, row, obs)
        return resource_doc(self.cfg, worker_id, row, obs.state, obs.out_of_memory)

    # -- operations ------------------------------------------------------

    def get(self, worker_id: str) -> dict[str, Any] | None:
        row = self.ledger.read(worker_id)
        if row is None or row.get("scope") != self.cfg.scope:
            return None
        return self._project(worker_id, row)

    def prepare(self, worker_id: str, reservation: dict[str, Any]) -> dict[str, Any]:
        self._validate_reservation(worker_id, reservation)
        row = self.ledger.read(worker_id)
        if row is not None:
            if row.get("scope") != self.cfg.scope:
                raise ProtocolError(409, "worker belongs to another scope")
            if row["state"] == STATE_DELETED:
                raise ProtocolError(409, "worker incarnation already terminated")
            if row["reservation"] != reservation:
                raise ProtocolError(409, "conflicting reservation for this worker")
            return self._project(worker_id, row)

        if self.ledger.count_live(self.cfg.scope) >= self.cfg.max_workers:
            raise ProtocolError(409, "capacity ceiling reached")

        row = {
            "scope": self.cfg.scope,
            "reservation": reservation,
            "resource_uid": new_resource_uid(),
            "handle": {},
            "state": STATE_PREPARED,
            "revision": 0,
            "created_at": time.time(),
            "started_at": None,
            "stopped_at": None,
            "deleted_at": None,
            "intent": None,
            "operation_id": uuid.uuid4().hex,
        }
        # Commit the reservation before any adapter work, so an interrupted
        # prepare converges on readback instead of orphaning a resource.
        self.ledger.write(worker_id, row)

        ctx = self._ctx(worker_id, row)
        try:
            handle = self.adapter.prepare(ctx, reservation)
        except Exception as exc:
            # Preparation is metadata-only by contract; leaving the row
            # prepared lets a retry re-run an idempotent prepare.
            raise ProtocolError(502, f"prepare failed: {type(exc).__name__}") from None
        row = self.ledger.read(worker_id) or row
        row["handle"] = handle or {}
        self.ledger.write(worker_id, row)
        return resource_doc(self.cfg, worker_id, row, STATE_PREPARED)

    def start(self, worker_id: str, body: dict[str, Any]) -> dict[str, Any]:
        row = self.ledger.read(worker_id)
        if row is None or row.get("scope") != self.cfg.scope:
            raise ProtocolError(404, "unknown worker")
        if row["state"] == STATE_DELETED:
            raise ProtocolError(409, "worker incarnation already terminated")
        if body.get("resource_uid") != row["resource_uid"]:
            raise ProtocolError(409, "resource UID does not match the prepared incarnation")
        if row["state"] in (STATE_STARTING, STATE_RUNNING):
            # Replay: never start a second launcher for one incarnation.
            return self._project(worker_id, row)
        if row["state"] == STATE_STOPPED:
            # Terminal. A stopped incarnation must never restart.
            raise ProtocolError(409, "worker incarnation already stopped")

        if row.get("intent") == "start":
            # A previous attempt may have launched a container whose response we
            # never saw. Observe before acting: a blind retry would start a
            # SECOND physical launcher for the same incarnation.
            ctx = self._ctx(worker_id, row)
            try:
                prior = self.adapter.observe(ctx, row.get("handle") or {})
            except Exception:
                raise ProtocolError(502, "start outcome unknown; provider unreachable") from None
            if prior.state in (STATE_STARTING, STATE_RUNNING):
                row = self._settle(worker_id, row, prior)
                return resource_doc(self.cfg, worker_id, row, row["state"])
            if prior.state in (STATE_STOPPED, STATE_FAILED):
                self._settle(worker_id, row, Observation(STATE_STOPPED, prior.handle, prior.out_of_memory))
                raise ProtocolError(409, "worker incarnation already stopped")

        row["intent"] = "start"
        row["operation_id"] = row.get("operation_id") or uuid.uuid4().hex
        self.ledger.write(worker_id, row)

        ctx = self._ctx(worker_id, row)
        started_at = time.time()
        try:
            obs = self.adapter.start(ctx, row.get("handle") or {})
        except Exception as exc:
            row = self.ledger.read(worker_id) or row
            # Keep the intent so reconciliation can determine whether the
            # launcher was accepted. Never guess, never re-launch blindly.
            row["last_error"] = type(exc).__name__
            self.ledger.write(worker_id, row)
            raise ProtocolError(502, f"start outcome unknown: {type(exc).__name__}") from None

        row = self.ledger.read(worker_id) or row
        row["started_at"] = started_at
        if obs.state == STATE_RUNNING and self.cfg.starting_grace_seconds > 0:
            obs = Observation(STATE_STARTING, obs.handle, obs.out_of_memory)
        row = self._settle(worker_id, row, obs)
        return resource_doc(self.cfg, worker_id, row, row["state"], bool(row.get("out_of_memory")))

    def remove(self, worker_id: str, body: dict[str, Any]) -> dict[str, Any]:
        row = self.ledger.read(worker_id)
        if row is None or row.get("scope") != self.cfg.scope:
            raise ProtocolError(404, "unknown worker")
        if body.get("resource_uid") != row["resource_uid"]:
            raise ProtocolError(409, "resource UID does not match this worker")
        if row["state"] == STATE_DELETED:
            return resource_doc(self.cfg, worker_id, row, STATE_DELETED)

        row["intent"] = "delete"
        self.ledger.write(worker_id, row)
        ctx = self._ctx(worker_id, row)
        try:
            self.adapter.release(ctx, row.get("handle") or {})
        except Exception as exc:
            row = self.ledger.read(worker_id) or row
            row["last_error"] = type(exc).__name__
            self.ledger.write(worker_id, row)
            raise ProtocolError(502, f"delete outcome unknown: {type(exc).__name__}") from None

        row = self.ledger.read(worker_id) or row
        row["state"] = STATE_DELETED
        row["deleted_at"] = row.get("deleted_at") or time.time()
        row["intent"] = None
        self.ledger.write(worker_id, row)
        return resource_doc(self.cfg, worker_id, row, STATE_DELETED, bool(row.get("out_of_memory")))

    # -- validation ------------------------------------------------------

    def _validate_reservation(self, worker_id: str, reservation: dict[str, Any]) -> None:
        for field_name in _RESERVATION_FIELDS:
            if field_name not in reservation:
                raise ProtocolError(400, f"reservation misses {field_name}")
        if reservation["worker_id"] != worker_id:
            raise ProtocolError(400, "reservation worker_id does not match the URL")
        if reservation["memory_limit_bytes"] != self.cfg.memory_limit_bytes:
            raise ProtocolError(409, "reservation memory does not match advertised capacity")
        if reservation["worker_version"] != self.cfg.worker_version:
            raise ProtocolError(409, "reservation build does not match advertised capacity")
        if int(reservation["max_lifetime_seconds"]) > self.cfg.max_lifetime_seconds:
            raise ProtocolError(409, "reservation lifetime exceeds advertised capacity")


# --------------------------------------------------------------------------
# HTTP transport
# --------------------------------------------------------------------------


class CapacityHandler(BaseHTTPRequestHandler):
    server_version = "BifrostCapacity/2.0"

    @property
    def engine(self) -> CapacityEngine:
        return self.server.engine  # type: ignore[attr-defined]

    @property
    def cfg(self) -> ProviderConfig:
        return self.server.cfg  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {self.command} {self.path} {fmt % args}\n")

    # -- plumbing --------------------------------------------------------

    def _send(self, status: int, body: dict[str, Any] | None = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except ValueError:
            raise ProtocolError(400, "invalid JSON body") from None

    def _authorized(self) -> bool:
        import hmac

        want = f"Bearer {self.cfg.token}".encode()
        got = (self.headers.get("Authorization") or "").encode()
        return hmac.compare_digest(want, got)

    def _scope_ok(self) -> bool:
        return (self.headers.get(SCOPE_HEADER) or "") == self.cfg.scope

    def _guard(self, *, require_scope: bool) -> str | None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return None
        if require_scope and not self._scope_ok():
            self._send(404, {"error": "unknown scope"})
            return None
        return "ok"

    @staticmethod
    def _uuid(value: str) -> str | None:
        try:
            return str(UUID(value)) == value and value or None
        except ValueError:
            return None

    def _worker_id(self) -> str | None:
        if not self.path.startswith("/v1/workers/"):
            return None
        raw = self.path[len("/v1/workers/"):]
        if "/" in raw:
            return None
        return self._uuid(raw)

    def _dispatch(self, method: str) -> None:
        try:
            if self.path == "/healthz":
                self._send(200, {"ok": True})
                return
            if self.path == "/v1/capabilities":
                # Scope is deliberately NOT required: the controller discovers
                # the scope from this response and sends no scope header.
                if self._guard(require_scope=False) is None:
                    return
                self._send(200, profile_doc(self.cfg))
                return

            worker_id = self._worker_id()
            if worker_id is None:
                self._send(404, {"error": "not found"})
                return
            if self._guard(require_scope=True) is None:
                return

            if method == "GET":
                doc = self.engine.get(worker_id)
            elif method == "PUT":
                doc = self.engine.prepare(worker_id, self._read_json())
            elif method == "DELETE":
                doc = self.engine.remove(worker_id, self._read_json())
            else:
                self._send(404, {"error": "not found"})
                return
            if doc is None:
                self._send(404, {"error": "unknown worker"})
                return
            self._send(200, doc)
        except ProtocolError as exc:
            self._send(exc.status, {"error": str(exc)})

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/v1/workers/") or not self.path.endswith("/start"):
            self._send(404, {"error": "not found"})
            return
        raw = self.path[len("/v1/workers/"): -len("/start")]
        self.path = f"/v1/workers/{raw}"
        worker_id = self._worker_id()
        self.path = f"/v1/workers/{raw}/start"
        if worker_id is None:
            self._send(404, {"error": "not found"})
            return
        if self._guard(require_scope=True) is None:
            return
        try:
            doc = self.engine.start(worker_id, self._read_json())
        except ProtocolError as exc:
            self._send(exc.status, {"error": str(exc)})
            return
        self._send(200, doc)


def build_server(cfg: ProviderConfig, engine: CapacityEngine) -> ThreadingHTTPServer:
    if not cfg.tls_cert_file or not cfg.tls_key_file:
        # The API dials HTTPS providers only; refuse to serve plaintext.
        raise SystemExit("TLS_CERT_FILE and TLS_KEY_FILE are required for a capacity provider")
    server = ThreadingHTTPServer(("0.0.0.0", cfg.port), CapacityHandler)
    server.engine = engine  # type: ignore[attr-defined]
    server.cfg = cfg  # type: ignore[attr-defined]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # The API dials HTTPS providers only; do not offer deprecated protocol
    # versions such as TLS 1.0/1.1 to that client.
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cfg.tls_cert_file, cfg.tls_key_file)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server


def serve(cfg: ProviderConfig, adapter: CapacityAdapter, store_path: str | None = None) -> int:
    ledger = Ledger(store_path)
    engine = CapacityEngine(cfg, adapter, ledger)
    server = build_server(cfg, engine)
    sys.stderr.write(
        f"capacity core: scope={cfg.scope} version={cfg.worker_version} "
        f"port={cfg.port} workers={len(ledger.rows)}\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C is the documented way to stop a foreground provider.
        pass
    return 0
