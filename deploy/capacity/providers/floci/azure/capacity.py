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
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "api"))

from capacity_core import (  # noqa: E402
    CapacityAdapter,
    Context,
    Observation,
    ProviderConfig,
    serve,
)
from shared.worker_control_auth import external_worker_environment  # noqa: E402

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
    def __init__(self, control_token: str) -> None:
        self.control_token = control_token
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
        env = external_worker_environment(
            self.env,
            self.control_token,
            UUID(ctx.worker_id),
            ctx.resource_uid,
            hostname=group_for(ctx.worker_id),
        )
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
        group = handle.get("group") or group_for(ctx.worker_id)
        found = self.aci.group(group)
        observed_handle = {**handle, "group": group}
        if found is None:
            return Observation("stopped" if handle.get("started") else "prepared", observed_handle)
        props = found.get("properties") or {}
        if props.get("provisioningState") == "Failed":
            return Observation("stopped", observed_handle)
        state = (props.get("instanceView") or {}).get("state")
        if state == "Running":
            return Observation("running", observed_handle)
        if state in ("Stopped", "Terminated"):
            return Observation("stopped", observed_handle)
        return Observation("starting", observed_handle)

    def release(self, ctx: Context, handle: dict) -> Observation:
        group = handle.get("group") or group_for(ctx.worker_id)
        self.aci.delete_group(group)
        found = self.aci.group(group)
        released_handle = {**handle, "group": group}
        if found is None:
            return Observation("deleted", released_handle)
        props = found.get("properties") or {}
        state = (props.get("instanceView") or {}).get("state")
        if props.get("provisioningState") == "Failed" or state in ("Stopped", "Terminated"):
            return Observation("stopped", released_handle)
        return Observation("running" if state == "Running" else "starting", released_handle)


def group_for(worker_id: str) -> str:
    return GROUP_PREFIX + worker_id.replace("-", "")[:20]


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> int:
    cfg = ProviderConfig.from_env()
    adapter = Adapter(cfg.token)
    return serve(cfg, adapter, os.environ.get("STORE_PATH") or None)


if __name__ == "__main__":
    raise SystemExit(main())
