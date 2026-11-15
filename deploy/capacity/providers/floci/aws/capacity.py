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
            raise EcsHttpError(action, exc.code) from None

    def task_definition(self, family: str) -> str | None:
        payload = self.call("ListTaskDefinitions", {
            "familyPrefix": family,
            "status": "ACTIVE",
            "sort": "DESC",
        })
        suffix = f"/{family}:"
        for arn in payload.get("taskDefinitionArns") or []:
            if isinstance(arn, str) and suffix in arn:
                return arn
        return None

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

    def run_task(self, cluster: str, family: str, started_by: str, client_token: str) -> str:
        payload = self.call("RunTask", {
            "cluster": cluster, "taskDefinition": family, "launchType": "EC2",
            "count": 1, "startedBy": started_by, "clientToken": client_token,
        })
        tasks = payload.get("tasks") or []
        if not tasks:
            raise RuntimeError("RunTask started nothing")
        return tasks[0]["taskArn"]

    def owned_tasks(self, cluster: str, started_by: str) -> list[str]:
        payload = self.call("ListTasks", {"cluster": cluster, "startedBy": started_by})
        return [arn for arn in payload.get("taskArns") or [] if isinstance(arn, str)]

    def describe_task(self, cluster: str, arn: str) -> dict | None:
        try:
            payload = self.call("DescribeTasks", {"cluster": cluster, "tasks": [arn]})
        except EcsHttpError as exc:
            if exc.status == 404:
                return None
            raise
        tasks = payload.get("tasks") or []
        return tasks[0] if tasks else None

    def stop_task(self, cluster: str, arn: str) -> None:
        try:
            self.call("StopTask", {"cluster": cluster, "task": arn, "reason": "capacity release"})
        except EcsHttpError as exc:
            if exc.status != 404:
                raise

    def deregister(self, arn: str) -> None:
        try:
            self.call("DeregisterTaskDefinition", {"taskDefinition": arn})
        except EcsHttpError as exc:
            if exc.status != 404:
                raise


class EcsHttpError(RuntimeError):
    def __init__(self, action: str, status: int) -> None:
        super().__init__(f"ECS {action} failed: HTTP {status}")
        self.status = status


class Adapter(CapacityAdapter):
    def __init__(self, control_token: str) -> None:
        self.control_token = control_token
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
        env = external_worker_environment(
            self.env,
            self.control_token,
            UUID(ctx.worker_id),
            ctx.resource_uid,
            hostname=family_for(ctx.worker_id),
        )
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
        family = family_for(ctx.worker_id, ctx.operation_id)
        arn = self.ecs.task_definition(family)
        if arn is None:
            arn = self.ecs.register_task_definition(family, self._container(ctx))
        return {"family": family, "task_definition_arn": arn, "started": False}

    def start(self, ctx: Context, handle: dict) -> Observation:
        task_arn = self.ecs.run_task(self.cluster, handle["family"], ctx.worker_id, ctx.operation_id)
        return Observation("starting", {**handle, "task_arn": task_arn, "started": True})

    def observe(self, ctx: Context, handle: dict) -> Observation:
        task_arn = handle.get("task_arn")
        if not task_arn:
            owned = self.ecs.owned_tasks(self.cluster, ctx.worker_id)
            task_arn = owned[0] if owned else None
        observed_handle = {**handle, "task_arn": task_arn, "started": True} if task_arn else handle
        if not task_arn:
            return Observation("prepared", observed_handle)
        task = self.ecs.describe_task(self.cluster, task_arn)
        if task is None:
            return Observation("stopped", observed_handle)
        last = task.get("lastStatus", "")
        if last in ("PROVISIONING", "PENDING", "ACTIVATING"):
            return Observation("starting", observed_handle)
        if last == "RUNNING":
            return Observation("running", observed_handle)
        return Observation("stopped", observed_handle)

    def release(self, ctx: Context, handle: dict) -> Observation:
        task_arns = [handle["task_arn"]] if handle.get("task_arn") else self.ecs.owned_tasks(self.cluster, ctx.worker_id)
        released_handle = dict(handle)
        for task_arn in task_arns:
            self.ecs.stop_task(self.cluster, task_arn)
            task = self.ecs.describe_task(self.cluster, task_arn)
            released_handle.update({"task_arn": task_arn, "started": True})
            if task is not None and task.get("lastStatus") != "STOPPED":
                state = "running" if task.get("lastStatus") == "RUNNING" else "starting"
                return Observation(state, released_handle)
        if handle.get("task_definition_arn"):
            self.ecs.deregister(handle["task_definition_arn"])
        return Observation("deleted", released_handle)


def family_for(worker_id: str, operation_id: str | None = None) -> str:
    worker = worker_id.replace("-", "")[:20]
    if not operation_id:
        return FAMILY_PREFIX + worker
    operation = "".join(char for char in operation_id if char.isalnum())[:20]
    return f"{FAMILY_PREFIX}{worker}-{operation}"


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
