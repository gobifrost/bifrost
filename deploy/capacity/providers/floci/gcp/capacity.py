"""Floci Cloud Run worker-pool adapter (emulator proof).

Only cloud-specific behaviour lives here; the protocol, ledger, validation, and
tombstones come from the shared core.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))

from capacity_core import (  # noqa: E402
    CapacityAdapter,
    Context,
    Observation,
    ProviderConfig,
    serve,
)

POOL_PREFIX = "bw-"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class FlociCloudRun:
    def __init__(self) -> None:
        self.base = _env("FLOCI_BASE_URL", "http://127.0.0.1:4588").rstrip("/")
        self.parent = f"projects/{_env('FLOCI_PROJECT', 'bifrost-capacity')}/locations/{_env('FLOCI_LOCATION', 'us-central1')}"

    def _call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base}/v2/{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode() or "{}")
            except ValueError:
                return exc.code, {}

    def create_pool(self, pool_id: str, container: dict) -> None:
        status, _ = self._call(
            "POST",
            f"{self.parent}/workerPools?workerPoolId={pool_id}",
            {"template": {"containers": [container]}, "scaling": {"manualInstanceCount": 0}},
        )
        if status not in (200, 201, 409):
            raise RuntimeError(f"floci create failed: HTTP {status}")

    def pool(self, pool_id: str) -> dict | None:
        status, body = self._call("GET", f"{self.parent}/workerPools/{pool_id}")
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"floci read failed: HTTP {status}")
        return body

    def scale(self, pool_id: str, count: int) -> None:
        status, _ = self._call(
            "PATCH",
            f"{self.parent}/workerPools/{pool_id}?updateMask=scaling.manualInstanceCount",
            {"scaling": {"manualInstanceCount": count}},
        )
        if status not in (200, 404):
            raise RuntimeError(f"floci scale failed: HTTP {status}")

    def delete_pool(self, pool_id: str) -> None:
        status, _ = self._call("DELETE", f"{self.parent}/workerPools/{pool_id}")
        if status not in (200, 202, 204, 404):
            raise RuntimeError(f"floci delete failed: HTTP {status}")


class Adapter(CapacityAdapter):
    def __init__(self) -> None:
        self.cloud = FlociCloudRun()
        self.image = _env("WORKER_IMAGE") or _require("WORKER_IMAGE")
        self.cpu = _env("WORKER_CPU", "2000m")
        self.memory = _env("WORKER_MEMORY", "4096Mi")
        try:
            self.env = json.loads(_env("WORKER_ENV_JSON", "{}"))
            self.command = json.loads(_env("WORKER_COMMAND_JSON", '["python"]'))
            self.args = json.loads(_env("WORKER_ARGS_JSON", '["-m", "src.worker.main"]'))
        except ValueError as exc:
            raise SystemExit(f"invalid WORKER_*_JSON: {exc}") from None

    def _container(self, ctx: Context) -> dict:
        env = dict(self.env)
        env.update({
            "BIFROST_WORKER_FORM": "external",
            "BIFROST_WORKER_INCARNATION_ID": ctx.worker_id,
            "BIFROST_WORKER_RESOURCE_UID": ctx.resource_uid,
            "BIFROST_POOL_WORKER_EXECUTION_LANE": "normal",
            "HOSTNAME": pool_id_for(ctx.worker_id),
        })
        return {
            "image": self.image,
            "command": self.command,
            "args": self.args,
            "env": [{"name": k, "value": str(v)} for k, v in sorted(env.items())],
            "resources": {"limits": {"cpu": self.cpu, "memory": self.memory}},
        }

    # -- adapter contract ------------------------------------------------

    def prepare(self, ctx: Context, spec: dict) -> dict:
        # Zero replicas: metadata only, no process.
        pool_id = pool_id_for(ctx.worker_id)
        self.cloud.create_pool(pool_id, self._container(ctx))
        return {"pool_id": pool_id, "started": False}

    def start(self, ctx: Context, handle: dict) -> Observation:
        pool_id = handle["pool_id"]
        self.cloud.scale(pool_id, 1)
        return Observation("starting", {**handle, "started": True})

    def observe(self, ctx: Context, handle: dict) -> Observation:
        pool_id = handle.get("pool_id") or pool_id_for(ctx.worker_id)
        pool = self.cloud.pool(pool_id)
        if pool is None:
            return Observation("stopped", {**handle, "pool_id": pool_id})
        terminal = pool.get("terminalCondition") or {}
        if terminal.get("state") == "CONDITION_FAILED":
            return Observation("stopped", {**handle, "pool_id": pool_id})
        replicas = int((pool.get("scaling") or {}).get("manualInstanceCount") or 0)
        if not handle.get("started"):
            return Observation("prepared", {**handle, "pool_id": pool_id})
        return Observation("running" if replicas > 0 else "stopped", {**handle, "pool_id": pool_id})

    def release(self, ctx: Context, handle: dict) -> Observation:
        pool_id = handle.get("pool_id") or pool_id_for(ctx.worker_id)
        self.cloud.delete_pool(pool_id)
        return Observation("deleted", {**handle, "pool_id": pool_id})


def pool_id_for(worker_id: str) -> str:
    return POOL_PREFIX + worker_id.replace("-", "")[:20]


def _require(name: str) -> str:
    value = _env(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> int:
    cfg = ProviderConfig.from_env()
    adapter = Adapter()
    return serve(cfg, adapter, _env("STORE_PATH") or None)


if __name__ == "__main__":
    raise SystemExit(main())
