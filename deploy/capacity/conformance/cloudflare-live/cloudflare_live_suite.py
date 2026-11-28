#!/usr/bin/env python3
"""Run the stock capacity suite with disposable normal-image API admission.

The generic suite is intentionally unchanged.  This wrapper replaces only its
Client constructor at process start, so normal capacity cases still use the
same HTTP driver and conformance reporting.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


CONTAINER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
BRIDGE_PATH = "/deploy/capacity/conformance/cloudflare-live/api_bridge.py"


class BridgeError(RuntimeError):
    pass


def _suite_module():
    path = Path(__file__).resolve().parents[1] / "conformance.py"
    spec = importlib.util.spec_from_file_location("bifrost_capacity_conformance", path)
    if spec is None or spec.loader is None:
        raise BridgeError("base conformance suite is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApiContainerBridge:
    def __init__(self, container: str, *, timeout_seconds: int = 30, ready_timeout_seconds: int = 180) -> None:
        if CONTAINER_RE.fullmatch(container) is None:
            raise BridgeError("BIFROST_CONFORMANCE_API_CONTAINER is invalid or missing")
        self.container = container
        self.timeout_seconds = timeout_seconds
        self.ready_timeout_seconds = ready_timeout_seconds

    @classmethod
    def from_environment(cls) -> "ApiContainerBridge":
        try:
            timeout = int(os.environ.get("BIFROST_CONFORMANCE_BRIDGE_TIMEOUT_SECONDS", "30"))
            ready_timeout = int(os.environ.get("BIFROST_CONFORMANCE_READY_TIMEOUT_SECONDS", "180"))
        except ValueError as exc:
            raise BridgeError("conformance bridge timeout is invalid") from exc
        if not 1 <= timeout <= 120 or not 1 <= ready_timeout <= 600:
            raise BridgeError("conformance bridge timeout is invalid")
        return cls(os.environ.get("BIFROST_CONFORMANCE_API_CONTAINER", ""),
                   timeout_seconds=timeout, ready_timeout_seconds=ready_timeout)

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(payload, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise BridgeError("bridge payload is invalid") from exc
        try:
            result = subprocess.run(
                ["docker", "exec", "-i", self.container, "python", BRIDGE_PATH],
                input=encoded,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError("API-container bridge was unavailable") from exc
        if result.returncode != 0:
            raise BridgeError("API-container bridge rejected the request")
        try:
            response = json.loads(result.stdout)
        except ValueError as exc:
            raise BridgeError("API-container bridge returned invalid output") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise BridgeError("API-container bridge rejected the request")
        return response

    def seed(self, payload: dict[str, Any]) -> None:
        self._call({"action": "seed", **payload})

    def wait_ready(self, payload: dict[str, Any]) -> None:
        deadline = time.monotonic() + self.ready_timeout_seconds
        while True:
            if self._call({"action": "ready", **payload}).get("ready") is True:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError("normal worker image did not complete authenticated API admission")
            time.sleep(min(1, remaining))


def image_client(base_client, bridge: ApiContainerBridge):
    """Make a Client that seeds only a freshly verified, prepared resource."""
    class ImageClient(base_client):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._prepared: dict[str, dict[str, Any]] = {}
            self._seeded: set[str] = set()

        def put(self, worker_id: str, reservation: dict[str, Any]) -> tuple[int, Any]:
            status, document = super().put(worker_id, reservation)
            if (
                status == 200
                and isinstance(document, dict)
                and document.get("worker_id") == worker_id
                and document.get("state") == "prepared"
                and isinstance(document.get("resource_uid"), str)
            ):
                self._prepared[worker_id] = dict(reservation)
            return status, document

        def start(self, worker_id: str, uid: str) -> tuple[int, Any]:
            # A fresh GET keeps this bridge from enrolling a wrong UID or a
            # replayed/stopped resource.  It never repairs an existing API row.
            status, resource = self.get(worker_id)
            reservation = self._prepared.get(worker_id)
            should_seed = (
                worker_id not in self._seeded
                and status == 200
                and isinstance(resource, dict)
                and resource.get("worker_id") == worker_id
                and resource.get("scope") == self.scope
                and resource.get("resource_uid") == uid
                and resource.get("state") == "prepared"
                and isinstance(reservation, dict)
                and resource.get("worker_version") == reservation.get("worker_version")
            )
            if should_seed:
                payload = {
                    "scope": self.scope,
                    "worker_id": worker_id,
                    "resource_uid": uid,
                    "reservation": reservation,
                }
                bridge.seed(payload)
                # Retain the mark even if POST /start is uncertain: retrying it
                # must not seed or reset a canonical external-worker row.
                self._seeded.add(worker_id)
                started = super().start(worker_id, uid)
                if started[0] == 200:
                    bridge.wait_ready(payload)
                return started
            return super().start(worker_id, uid)
    return ImageClient


def main() -> int:
    suite = _suite_module()
    if "--help" in sys.argv or "-h" in sys.argv:
        return suite.main()
    bridge = ApiContainerBridge.from_environment()
    original_client = suite.Client
    suite.Client = image_client(original_client, bridge)
    try:
        return suite.main()
    finally:
        suite.Client = original_client


if __name__ == "__main__":
    raise SystemExit(main())
