#!/usr/bin/env python3
"""Self-hosted/local control service for `bifrost-sandbox-runner`."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import runner

MAX_BODY_BYTES = 1024 * 1024
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8300
RUNNER_PATH = "/usr/local/bin/bifrost-sandbox-runner"
INSTANCE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
MAX_ROUTE_SEGMENTS = 3


class ServiceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


class RunnerServiceError(ServiceError):
    pass


@dataclass
class JobRecord:
    run_id: str
    instance_id: str
    envelope: runner.Envelope
    directory: Path
    status: str
    state_path: Path
    envelope_path: Path
    log_path: Path
    created_at: float
    pid: int | None = None
    return_code: int | None = None
    process: subprocess.Popen[bytes] | None = None


class RunnerManager:
    def __init__(self, *, secret: str, workdir: Path, max_concurrent: int = 2) -> None:
        self.secret = secret
        self.workdir = Path(workdir)
        self.max_concurrent = max(1, max_concurrent)
        self._lock = threading.Lock()
        self._runs: dict[str, JobRecord] = {}
        self._root = self.workdir / "jobs"
        self._runner_root = self.workdir / "runner-home"
        self._root.mkdir(parents=True, exist_ok=True)
        self._runner_root.mkdir(parents=True, exist_ok=True)

    def _instance_path(self, instance_id: str) -> Path:
        if not INSTANCE_ID_RE.fullmatch(instance_id):
            raise RunnerServiceError(HTTPStatus.BAD_REQUEST, "instance_id is invalid")
        return self._root / instance_id

    def _run_id(self, instance_id: str, envelope: runner.Envelope) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "instance_id": instance_id,
                    "job": envelope.__dict__,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        namespace = uuid.UUID("8e7f2b00-3ed4-4b2f-8d45-8e8a8b0e3e00")
        return str(uuid.uuid5(namespace, f"{instance_id}:{digest}"))

    def _run_directory(self, instance_id: str, run_id: str) -> Path:
        return self._instance_path(instance_id) / run_id

    def _running_count(self) -> int:
        return sum(
            1
            for run in self._runs.values()
            if run.status in {"queued", "running"}
        )

    def _write_state(self, record: JobRecord, *, status: str | None = None, pid: int | None = None,
                     return_code: int | None = None) -> None:
        data = {
            "run_id": record.run_id,
            "instance_id": record.instance_id,
            "created_at": record.created_at,
            "updated_at": time.time(),
            "status": status or record.status,
            "pid": pid if pid is not None else record.pid,
            "return_code": return_code if return_code is not None else record.return_code,
        }
        record.status = data["status"]
        record.pid = data["pid"]
        record.return_code = data["return_code"]
        # Cancellation and the worker's process watcher can persist the same
        # transition concurrently. Give each atomic replacement its own
        # staging file so one writer cannot rename the other's temporary file.
        tmp = record.state_path.with_name(
            f".{record.state_path.name}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        tmp.replace(record.state_path)

    def _load_record(self, state_path: Path, run_id: str, instance_id: str) -> JobRecord | None:
        envelope_path = state_path.parent / "job.json"
        if not state_path.is_file() or not envelope_path.is_file():
            return None
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            envelope = runner.Envelope.parse(envelope_path.read_bytes())
        except Exception:
            return None
        if not isinstance(state, dict):
            return None
        return JobRecord(
            run_id=run_id,
            instance_id=instance_id,
            envelope=envelope,
            directory=state_path.parent,
            status=state.get("status", "unknown"),
            state_path=state_path,
            envelope_path=envelope_path,
            log_path=state_path.parent / "runner.log",
            created_at=float(state.get("created_at", time.time())),
            pid=state.get("pid"),
            return_code=state.get("return_code"),
        )

    def _load_record_by_id(self, run_id: str) -> JobRecord | None:
        for instance_dir in self._root.iterdir():
            state = instance_dir / run_id / "state.json"
            if state.is_file():
                return self._load_record(state, run_id, instance_dir.name)
        return None

    def _record_for(self, run_id: str) -> JobRecord | None:
        if run_id in self._runs:
            return self._runs[run_id]
        record = self._load_record_by_id(run_id)
        if record is not None:
            self._runs[run_id] = record
        return record

    def _run_worker(self, record: JobRecord) -> None:
        with open(record.log_path, "ab") as stream:
            try:
                process = subprocess.Popen(
                    [RUNNER_PATH, str(record.envelope_path)],
                    cwd=str(self._runner_root),
                    env={
                        **os.environ,
                        "BIFROST_RUNNER_WORKDIR": str(record.directory),
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except FileNotFoundError:
                self._write_state(record, status="failed")
                with self._lock:
                    active = self._runs.get(record.run_id)
                    if active is not None:
                        active.status = record.status
                        active.process = None
                return
            record.process = process
            self._write_state(record, status="running", pid=process.pid)

            rc = process.wait()
            if rc == 0:
                status = "succeeded"
            elif rc == runner.REPORTED_CANCELLED_EXIT:
                status = "cancelled"
            else:
                status = "failed"
            self._write_state(record, status=status, return_code=rc)

        with self._lock:
            active = self._runs.get(record.run_id)
            if active is not None:
                active.status = record.status
                active.return_code = record.return_code
                active.process = None
                active.pid = None

    def submit(self, instance_id: str, raw_job: bytes) -> tuple[JobRecord, bool]:
        try:
            envelope = runner.Envelope.parse(raw_job)
        except runner.RunnerError as exc:
            raise RunnerServiceError(HTTPStatus.BAD_REQUEST, exc.message)

        run_id = self._run_id(instance_id, envelope)

        with self._lock:
            existing = self._record_for(run_id)
            if existing is not None:
                return existing, False

            if self._running_count() >= self.max_concurrent:
                raise RunnerServiceError(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    "concurrency limit reached",
                )

            run_dir = self._run_directory(instance_id, run_id)
            run_dir.mkdir(parents=True)
            envelope_path = run_dir / "job.json"
            state_path = run_dir / "state.json"
            envelope_path.write_text(json.dumps(envelope.__dict__, sort_keys=True), encoding="utf-8")

            record = JobRecord(
                run_id=run_id,
                instance_id=instance_id,
                envelope=envelope,
                directory=run_dir,
                status="queued",
                state_path=state_path,
                envelope_path=envelope_path,
                log_path=run_dir / "runner.log",
                created_at=time.time(),
            )
            self._runs[run_id] = record
            self._write_state(record, status="queued")

            worker = threading.Thread(target=self._run_worker, args=(record,), daemon=True)
            worker.start()
            return self._runs[run_id], True

    def terminate(self, run_id: str) -> JobRecord:
        try:
            uuid.UUID(run_id)
        except ValueError as exc:
            raise RunnerServiceError(HTTPStatus.BAD_REQUEST, "run_id is not valid") from exc

        with self._lock:
            record = self._record_for(run_id)
            if record is None:
                raise RunnerServiceError(HTTPStatus.NOT_FOUND, "run_id not found")

            if record.status == "queued":
                self._write_state(record, status="cancelled")
                return record

            if record.status not in {"running", "cancelled", "failed", "succeeded"}:
                return record

            if record.status in {"cancelled", "failed", "succeeded"}:
                return record

            process = record.process
            if process is None:
                self._write_state(record, status="cancelled")
                return record

            if process.poll() is not None:
                record.return_code = process.returncode
                if record.return_code == runner.REPORTED_CANCELLED_EXIT:
                    self._write_state(record, status="cancelled")
                elif record.return_code == 0:
                    self._write_state(record, status="succeeded")
                else:
                    self._write_state(record, status="failed")
                return record

            try:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            except Exception as exc:
                self._write_state(record, status="failed")
                raise RunnerServiceError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "could not terminate run",
                ) from exc

            self._write_state(record, status="cancelled")
            return record

    def inspect(self, run_id: str) -> JobRecord:
        with self._lock:
            record = self._record_for(run_id)
            if record is None:
                raise RunnerServiceError(HTTPStatus.NOT_FOUND, "run_id not found")
            return record

    def provision(self) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [RUNNER_PATH, "--probe"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RunnerServiceError(HTTPStatus.SERVICE_UNAVAILABLE, "runner binary unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise RunnerServiceError(HTTPStatus.GATEWAY_TIMEOUT, "runner probe timed out") from exc

        if result.returncode != 0:
            detail = (result.stdout or result.stderr or "").strip()
            raise RunnerServiceError(HTTPStatus.SERVICE_UNAVAILABLE, f"probe failed: {detail}")

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RunnerServiceError(HTTPStatus.SERVICE_UNAVAILABLE, "probe response invalid") from exc

        if payload.get("schema_version") != runner.SCHEMA_VERSION:
            raise RunnerServiceError(HTTPStatus.SERVICE_UNAVAILABLE, "schema mismatch")
        if payload.get("harness") != "opencode":
            raise RunnerServiceError(HTTPStatus.SERVICE_UNAVAILABLE, "unsupported harness")
        if payload.get("ready") is not True:
            raise RunnerServiceError(HTTPStatus.SERVICE_UNAVAILABLE, "runner not ready")
        return payload


class RunnerRequestHandler(BaseHTTPRequestHandler):
    server: "RunnerServiceServer"

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        try:
            if self.path == "/health":
                self._authenticate()
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "ready": True,
                        "running": self.server.manager._running_count(),
                        "max_concurrent": self.server.manager.max_concurrent,
                    },
                )
                return

            if self.path.startswith("/jobs/"):
                self._authenticate()
                path = self.path.split("?", 1)[0].rstrip("/")
                parts = path.split("/")
                if len(parts) != MAX_ROUTE_SEGMENTS or parts[1] != "jobs" or not parts[2]:
                    raise RunnerServiceError(HTTPStatus.NOT_FOUND, "not found")
                record = self.server.manager.inspect(parts[2])
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "run_id": record.run_id,
                        "status": record.status,
                        "return_code": record.return_code,
                        "pid": record.pid,
                    },
                )
                return

            raise RunnerServiceError(HTTPStatus.NOT_FOUND, "not found")
        except ServiceError as exc:
            self._write_json(exc.status, {"error": exc.message})

    def do_POST(self) -> None:
        try:
            self._authenticate()
            if self.path == "/provision":
                payload = self.server.manager.provision()
                self._write_json(HTTPStatus.OK, payload)
                return

            if self.path != "/jobs":
                raise RunnerServiceError(HTTPStatus.NOT_FOUND, "not found")

            body = self._read_body()
            request = self._load_json(body)
            if not isinstance(request, dict):
                raise RunnerServiceError(HTTPStatus.BAD_REQUEST, "request body must be an object")
            keys = set(request)
            if keys != {"instance_id", "job"}:
                raise RunnerServiceError(HTTPStatus.BAD_REQUEST, "request must include instance_id and job")
            if not isinstance(request["instance_id"], str) or not isinstance(request["job"], dict):
                raise RunnerServiceError(HTTPStatus.BAD_REQUEST, "instance_id and job have invalid type")

            record, created = self.server.manager.submit(
                request["instance_id"],
                json.dumps(request["job"], sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
            status = HTTPStatus.ACCEPTED if created else HTTPStatus.OK
            self._write_json(
                status,
                {
                    "run_id": record.run_id,
                    "status": record.status,
                },
            )
        except ServiceError as exc:
            self._write_json(exc.status, {"error": exc.message})

    def do_DELETE(self) -> None:
        try:
            self._authenticate()
            path = self.path.split("?", 1)[0].rstrip("/")
            parts = path.split("/")
            if len(parts) != MAX_ROUTE_SEGMENTS or parts[1] != "jobs" or not parts[2]:
                raise RunnerServiceError(HTTPStatus.NOT_FOUND, "not found")
            run_id = parts[2]
            record = self.server.manager.terminate(run_id)
            self._write_json(
                HTTPStatus.OK,
                {
                    "run_id": record.run_id,
                    "status": record.status,
                },
            )
        except ServiceError as exc:
            self._write_json(exc.status, {"error": exc.message})

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if length is None:
            raise RunnerServiceError(HTTPStatus.LENGTH_REQUIRED, "content-length required")
        try:
            size = int(length)
        except ValueError as exc:
            raise RunnerServiceError(HTTPStatus.BAD_REQUEST, "invalid content-length") from exc
        if size > MAX_BODY_BYTES:
            raise RunnerServiceError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
        return self.rfile.read(size)

    def _load_json(self, payload: bytes) -> Any:
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RunnerServiceError(HTTPStatus.BAD_REQUEST, "invalid JSON") from exc

    def _authenticate(self) -> None:
        expected = self.server.manager.secret.encode("utf-8")
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or not header[len("Bearer "):]:
            raise RunnerServiceError(HTTPStatus.UNAUTHORIZED, "missing bearer token")
        token = header[len("Bearer "):]
        if not hmac.compare_digest(expected, token.encode("utf-8")):
            raise RunnerServiceError(HTTPStatus.FORBIDDEN, "invalid token")

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        response = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args: object) -> None:
        return


class RunnerServiceServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], manager: RunnerManager) -> None:
        super().__init__(server_address, RunnerRequestHandler)
        self.manager = manager


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--max-jobs", type=int, default=2)
    args = parser.parse_args(argv)

    secret = os.environ.get("BIFROST_RUNNER_SECRET")
    if not secret:
        print("BIFROST_RUNNER_SECRET must be set")
        return 1

    manager = RunnerManager(
        secret=secret,
        workdir=Path(os.environ.get("BIFROST_RUNNER_WORKDIR", "/work")),
        max_concurrent=args.max_jobs,
    )

    try:
        manager.provision()
    except RunnerServiceError as exc:
        print(f"provision failed: {exc.message}")
        return 1

    print(f"runner service listening on {args.host}:{args.port}")
    server = RunnerServiceServer((args.host, args.port), manager)
    try:
        server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
