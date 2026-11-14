"""Floci AWS ECS adapter (emulator proof).

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

FAMILY_PREFIX = "bw-"
CONTAINER_NAME = "worker"
TARGET = "AmazonEC2ContainerServiceV20141113"


class Ecs:
    def __init__(self) -> None:
        self.base = os.environ.get("FLOCI_BASE_URL", "http://127.0.0.1:4566").rstrip("/")

    def call(self, action: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base}/",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": f"{TARGET}.{action}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ECS {action} failed: HTTP {exc.code}") from None

    def ensure_cluster(self, name: str) -> None:
        self.call("CreateCluster", {"clusterName": name})

    def register_task_definition(self, family: str, container: dict) -> str:
        payload = self.call("RegisterTaskDefinition", {
            "family": family,
            "networkMode": "bridge",
            "requiresCompatibilities": ["EC2"],
            "containerDefinitions": [container],
        })
        arn = (payload.get("taskDefinition") or {}).get("taskDefinitionArn")
        if not arn:
            raise RuntimeError("RegisterTaskDefinition returned no ARN")
        return arn

    def run_task(self, cluster: str, family: str, started_by: str) -> str:
        payload = self.call("RunTask", {
            "cluster": cluster, "taskDefinition": family, "launchType": "EC2",
            "count": 1, "startedBy": started_by,
        })
        tasks = payload.get("tasks") or []
        if not tasks:
            raise RuntimeError("RunTask started nothing")
        return tasks[0]["taskArn"]

    def describe_task(self, cluster: str, arn: str) -> dict | None:
        payload = self.call("DescribeTasks", {"cluster": cluster, "tasks": [arn]})
        tasks = payload.get("tasks") or []
        return tasks[0] if tasks else None

    def stop_task(self, cluster: str, arn: str) -> None:
        try:
            self.call("StopTask", {"cluster": cluster, "task": arn, "reason": "capacity release"})
        except RuntimeError:
            # The task may already be gone; release is a best-effort stop.
            pass

    def deregister(self, arn: str) -> None:
        try:
            self.call("DeregisterTaskDefinition", {"taskDefinition": arn})
        except RuntimeError:
            # Already deregistered: the desired end state holds.
            pass


class Adapter(CapacityAdapter):
    def __init__(self) -> None:
        self.ecs = Ecs()
        self.cluster = os.environ.get("FLOCI_CLUSTER", "bifrost-capacity")
        self.image = _require("WORKER_IMAGE")
        self.cpu_units = int(os.environ.get("WORKER_CPU_UNITS", "2048"))
        self.memory_mib = int(os.environ.get("WORKER_MEMORY_MIB", "4096"))
        try:
            self.env = json.loads(os.environ.get("WORKER_ENV_JSON", "{}"))
            self.entrypoint = json.loads(os.environ.get("WORKER_ENTRYPOINT_JSON", '["python"]'))
            self.command = json.loads(os.environ.get("WORKER_COMMAND_JSON", '["-m", "src.worker.main"]'))
        except ValueError as exc:
            raise SystemExit(f"invalid WORKER_*_JSON: {exc}") from None
        self.ecs.ensure_cluster(self.cluster)

    def _container(self, ctx: Context) -> dict:
        env = dict(self.env)
        env.update({
            "BIFROST_WORKER_FORM": "external",
            "BIFROST_WORKER_INCARNATION_ID": ctx.worker_id,
            "BIFROST_WORKER_RESOURCE_UID": ctx.resource_uid,
            "BIFROST_POOL_WORKER_EXECUTION_LANE": "normal",
            "HOSTNAME": family_for(ctx.worker_id),
        })
        return {
            "name": CONTAINER_NAME,
            "image": self.image,
            "entryPoint": self.entrypoint,
            "command": self.command,
            "environment": [{"name": k, "value": str(v)} for k, v in sorted(env.items())],
            "memory": self.memory_mib,
            "cpu": self.cpu_units,
            "essential": True,
        }

    # -- adapter contract ------------------------------------------------

    def prepare(self, ctx: Context, spec: dict) -> dict:
        # Registers a task definition only: no task runs.
        family = family_for(ctx.worker_id)
        arn = self.ecs.register_task_definition(family, self._container(ctx))
        return {"family": family, "task_definition_arn": arn, "started": False}

    def start(self, ctx: Context, handle: dict) -> Observation:
        task_arn = self.ecs.run_task(self.cluster, handle["family"], ctx.worker_id)
        return Observation("starting", {**handle, "task_arn": task_arn, "started": True})

    def observe(self, ctx: Context, handle: dict) -> Observation:
        if not handle.get("started"):
            return Observation("prepared", handle)
        task_arn = handle.get("task_arn")
        if not task_arn:
            return Observation("stopped", handle)
        task = self.ecs.describe_task(self.cluster, task_arn)
        if task is None:
            return Observation("stopped", handle)
        last = task.get("lastStatus", "")
        if last in ("PROVISIONING", "PENDING", "ACTIVATING"):
            return Observation("starting", handle)
        if last == "RUNNING":
            return Observation("running", handle)
        return Observation("stopped", handle)

    def release(self, ctx: Context, handle: dict) -> Observation:
        if handle.get("task_arn"):
            self.ecs.stop_task(self.cluster, handle["task_arn"])
        if handle.get("task_definition_arn"):
            self.ecs.deregister(handle["task_definition_arn"])
        return Observation("deleted", handle)


def family_for(worker_id: str) -> str:
    return FAMILY_PREFIX + worker_id.replace("-", "")[:20]


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
