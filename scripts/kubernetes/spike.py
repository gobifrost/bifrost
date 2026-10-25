"""Shared helpers for the local Kind experiments in this directory."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / "scripts/kubernetes/local-kind.sh"
NS = "bifrost-local"


def kubectl(*args: str, data: str | None = None) -> str:
    result = subprocess.run(
        [str(LOCAL), "kubectl", "--", "-n", NS, *args], input=data,
        text=True, capture_output=True, check=True, timeout=60,
    )
    return result.stdout


def wait_for_rollout(resource: str, timeout: float) -> None:
    """Observe a deployment with short requests within its rollout deadline."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deployment = json.loads(kubectl("get", resource, "-o", "json", "--request-timeout=10s"))
        status = deployment.get("status", {})
        observed = status.get("observedGeneration", 0) >= deployment["metadata"]["generation"]
        for condition in status.get("conditions", []):
            if observed and condition.get("type") == "Progressing" and condition.get("reason") == "ProgressDeadlineExceeded":
                raise RuntimeError(f"{resource}: {condition.get('message', 'rollout failed')}")
        desired = deployment["spec"].get("replicas", 1)
        if (
            observed
            and status.get("updatedReplicas", 0) == desired
            and status.get("replicas", 0) == desired
            and status.get("availableReplicas", 0) == desired
        ):
            return
        time.sleep(1)
    raise TimeoutError(f"{resource} did not finish its rollout within {timeout}s")
