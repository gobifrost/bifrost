"""Floci Azure Container Instances adapter (emulator proof).

Only cloud-specific behaviour lives here; the protocol, ledger, validation, and
tombstones come from the shared core.

Note the prepare contract: ACI has no "create without starting", so prepare is
ledger-only and the group is created at start. An earlier revision created the
group running and then stopped it, which started a process during prepare and
violated the protocol.
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

GROUP_PREFIX = "bw-"
CONTAINER_NAME = "worker"
API_VERSION = "2023-05-01"


class Aci:
    def __init__(self) -> None:
        self.base = os.environ.get("FLOCI_BASE_URL", "http://127.0.0.1:4577").rstrip("/")
        self.sub = os.environ.get("FLOCI_AZ_SUBSCRIPTION", "bifrost-capacity")
        self.rg = os.environ.get("FLOCI_AZ_RESOURCE_GROUP", "bifrost-capacity")
        self.location = os.environ.get("FLOCI_AZ_LOCATION", "eastus")

    def url(self, group: str, suffix: str = "") -> str:
        return (
            f"{self.base}/subscriptions/{self.sub}/resourceGroups/{self.rg}"
            f"/providers/Microsoft.ContainerInstance/containerGroups/{group}{suffix}"
            f"?api-version={API_VERSION}"
        )

    def call(self, method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode() or "{}"
                try:
                    return resp.status, json.loads(raw)
                except ValueError:
                    return resp.status, {}
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode() or "{}")
            except ValueError:
                return exc.code, {}

    def create_group(self, group: str, container: dict) -> None:
        status, body = self.call("PUT", self.url(group), {
            "location": self.location,
            "properties": {"containers": [container], "osType": "Linux"},
        })
        if status not in (200, 201):
            raise RuntimeError(f"ACI create failed: HTTP {status} {json.dumps(body)[:120]}")

    def group(self, group: str) -> dict | None:
        status, body = self.call("GET", self.url(group))
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"ACI read failed: HTTP {status}")
        return body

    def delete_group(self, group: str) -> None:
        status, _ = self.call("DELETE", self.url(group))
        if status not in (200, 202, 204, 404):
            raise RuntimeError(f"ACI delete failed: HTTP {status}")


class Adapter(CapacityAdapter):
    def __init__(self) -> None:
        self.aci = Aci()
        self.image = _require("WORKER_IMAGE")
        self.cpu = float(os.environ.get("WORKER_CPU", "2.0"))
        self.memory_gb = float(os.environ.get("WORKER_MEMORY_GB", "4.0"))
        try:
            self.env = json.loads(os.environ.get("WORKER_ENV_JSON", "{}"))
            self.command = json.loads(os.environ.get("WORKER_COMMAND_JSON", '["python", "-m", "src.worker.main"]'))
        except ValueError as exc:
            raise SystemExit(f"invalid WORKER_*_JSON: {exc}") from None

    def _container(self, ctx: Context) -> dict:
        env = dict(self.env)
        env.update({
            "BIFROST_WORKER_FORM": "external",
            "BIFROST_WORKER_INCARNATION_ID": ctx.worker_id,
            "BIFROST_WORKER_RESOURCE_UID": ctx.resource_uid,
            "BIFROST_POOL_WORKER_EXECUTION_LANE": "normal",
            "HOSTNAME": group_for(ctx.worker_id),
        })
        return {
            "name": CONTAINER_NAME,
            "properties": {
                # In ACI, command replaces the image entrypoint.
                "image": self.image,
                "command": self.command,
                "environmentVariables": [{"name": k, "value": str(v)} for k, v in sorted(env.items())],
                "resources": {"requests": {"cpu": self.cpu, "memoryInGB": self.memory_gb}},
            },
        }

    # -- adapter contract ------------------------------------------------

    def prepare(self, ctx: Context, spec: dict) -> dict:
        # Ledger-only: ACI cannot create a group without starting it.
        return {"group": group_for(ctx.worker_id), "started": False}

    def start(self, ctx: Context, handle: dict) -> Observation:
        group = handle.get("group") or group_for(ctx.worker_id)
        self.aci.create_group(group, self._container(ctx))
        return Observation("starting", {**handle, "group": group, "started": True})

    def observe(self, ctx: Context, handle: dict) -> Observation:
        if not handle.get("started"):
            return Observation("prepared", handle)
        group = handle.get("group") or group_for(ctx.worker_id)
        found = self.aci.group(group)
        if found is None:
            return Observation("stopped", {**handle, "group": group})
        props = found.get("properties") or {}
        if props.get("provisioningState") == "Failed":
            return Observation("stopped", {**handle, "group": group})
        state = (props.get("instanceView") or {}).get("state")
        if state == "Running":
            return Observation("running", {**handle, "group": group})
        return Observation("stopped", {**handle, "group": group})

    def release(self, ctx: Context, handle: dict) -> Observation:
        group = handle.get("group") or group_for(ctx.worker_id)
        self.aci.delete_group(group)
        return Observation("deleted", {**handle, "group": group})


def group_for(worker_id: str) -> str:
    return GROUP_PREFIX + worker_id.replace("-", "")[:20]


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> int:
    cfg = ProviderConfig.from_env()
    adapter = Adapter()
    return serve(cfg, adapter, os.environ.get("STORE_PATH") or None)


if __name__ == "__main__":
    raise SystemExit(main())
