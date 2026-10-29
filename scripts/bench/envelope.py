"""Resource-envelope arithmetic for quota-bound benchmark profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_CPU = re.compile(r"^(?P<value>(?:\d+(?:\.\d*)?|\.\d+))(?P<milli>m)?$")
_MEMORY = re.compile(r"^(?P<value>(?:\d+(?:\.\d*)?|\.\d+))(?P<unit>Ki|Mi|Gi|Ti|K|M|G|T)?$")
_MEMORY_UNITS = {
    None: 1,
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "K": 1000,
    "M": 1000**2,
    "G": 1000**3,
    "T": 1000**4,
}
_GIB = 1024**3


class EnvelopeError(Exception):
    """A benchmark resource envelope is invalid or does not fit."""


def _quantity(value: object, pattern: re.Pattern[str], label: str) -> tuple[Decimal, str | None]:
    if isinstance(value, bool):
        raise EnvelopeError(f"invalid {label} quantity {value!r}")
    match = pattern.fullmatch(str(value))
    if not match:
        raise EnvelopeError(f"invalid {label} quantity {value!r}")
    try:
        return Decimal(match["value"]), match.groupdict().get("unit", match.groupdict().get("milli"))
    except InvalidOperation as error:
        raise EnvelopeError(f"invalid {label} quantity {value!r}") from error


def _whole(value: Decimal, label: str, original: object) -> int:
    if value != value.to_integral_value():
        raise EnvelopeError(f"invalid {label} quantity {original!r}")
    return int(value)


def parse_cpu(quantity: object) -> int:
    """Parse a Kubernetes CPU quantity into millicores."""
    value, milli = _quantity(quantity, _CPU, "CPU")
    return _whole(value if milli else value * 1000, "CPU", quantity)


def parse_memory(quantity: object) -> int:
    """Parse a Kubernetes memory quantity into bytes."""
    value, unit = _quantity(quantity, _MEMORY, "memory")
    return _whole(value * _MEMORY_UNITS[unit], "memory", quantity)


@dataclass(frozen=True)
class Resources:
    cpu_m: int
    memory: int

    def __add__(self, other: Resources) -> Resources:
        return Resources(self.cpu_m + other.cpu_m, self.memory + other.memory)

    def __mul__(self, scalar: int) -> Resources:
        if not isinstance(scalar, int) or isinstance(scalar, bool):
            raise EnvelopeError("resource scalar must be an integer")
        return Resources(self.cpu_m * scalar, self.memory * scalar)

    __rmul__ = __mul__

    def fits(self, other: Resources) -> bool:
        """Return whether these resources are no larger than ``other``."""
        return self.cpu_m <= other.cpu_m and self.memory <= other.memory

    def format(self) -> str:
        """Format CPU and memory for an envelope report."""
        return f"{self.cpu_m / 1000:.3f} CPU / {self.memory / _GIB:.2f} GiB"


def container_resources(container: dict) -> Resources:
    """Return a container's equal CPU and memory request/limit pair."""
    name = str(container.get("name", "<unnamed>"))
    resources = container.get("resources", {})
    requests = resources.get("requests")
    limits = resources.get("limits")
    if not isinstance(requests, dict) or not isinstance(limits, dict):
        raise EnvelopeError(f"container {name} must set requests and limits")
    if any(key not in requests or key not in limits for key in ("cpu", "memory")):
        raise EnvelopeError(f"container {name} must set CPU and memory requests and limits")
    try:
        requested = Resources(parse_cpu(requests["cpu"]), parse_memory(requests["memory"]))
        limited = Resources(parse_cpu(limits["cpu"]), parse_memory(limits["memory"]))
    except EnvelopeError as error:
        raise EnvelopeError(f"container {name}: {error}") from error
    if requested != limited:
        raise EnvelopeError(f"container {name} must have requests equal to limits")
    return requested


def _maximum(left: Resources, right: Resources) -> Resources:
    return Resources(max(left.cpu_m, right.cpu_m), max(left.memory, right.memory))


def pod_effective(pod_spec: dict, name: str) -> Resources:
    """Kubernetes effective request: the larger of the largest init container and the containers' sum.

    Restartable (sidecar) init containers change that rule; none run in the
    tenant, so they are refused rather than approximated.
    """
    containers = pod_spec.get("containers", [])
    if not containers:
        raise EnvelopeError(f"pod {name} has no containers")
    total = Resources(0, 0)
    for container in containers:
        total += container_resources(container)
    init_max = Resources(0, 0)
    for container in pod_spec.get("initContainers", []):
        if container.get("restartPolicy") == "Always":
            raise EnvelopeError(f"pod {name} has sidecar init container {container.get('name')}; not supported")
        init_max = _maximum(init_max, container_resources(container))
    return _maximum(init_max, total)


def max_occupancy(fixed: dict[str, Resources], worker: Resources, max_workers: int) -> Resources:
    """Return fixed-component resources plus every permitted worker Job."""
    if max_workers < 0:
        raise EnvelopeError("max workers must not be negative")
    return sum(fixed.values(), Resources(0, 0)) + worker * max_workers


def check_fits(occupancy: Resources, quota_hard: dict) -> Resources:
    """Require maximum occupancy to fit every request and limit quota."""
    keys = ("requests.cpu", "limits.cpu", "requests.memory", "limits.memory")
    missing = [key for key in keys if key not in quota_hard]
    if missing:
        raise EnvelopeError(f"quota is missing required keys: {', '.join(missing)}")
    values = {
        "requests.cpu": parse_cpu(quota_hard["requests.cpu"]),
        "limits.cpu": parse_cpu(quota_hard["limits.cpu"]),
        "requests.memory": parse_memory(quota_hard["requests.memory"]),
        "limits.memory": parse_memory(quota_hard["limits.memory"]),
    }
    overflow: list[str] = []
    for key in ("requests.cpu", "limits.cpu"):
        if occupancy.cpu_m > values[key]:
            overflow.append(f"{key} exceeds by {occupancy.cpu_m - values[key]}m")
    for key in ("requests.memory", "limits.memory"):
        if occupancy.memory > values[key]:
            overflow.append(f"{key} exceeds by {occupancy.memory - values[key]} bytes")
    if overflow:
        raise EnvelopeError(f"maximum occupancy exceeds quota: {', '.join(overflow)}")
    return Resources(
        min(values["requests.cpu"], values["limits.cpu"]) - occupancy.cpu_m,
        min(values["requests.memory"], values["limits.memory"]) - occupancy.memory,
    )


def reconcile(pods: list[dict], quota_status: dict, tolerance_m: int = 0, tolerance_bytes: int = 0) -> dict:
    """Compare non-terminal effective pod requests with ResourceQuota status.used."""
    computed = Resources(0, 0)
    for pod in pods:
        if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
            continue
        metadata = pod.get("metadata", {})
        computed += pod_effective(pod.get("spec", {}), str(metadata.get("name", "<unnamed>")))
    used = quota_status.get("used", {})
    if "requests.cpu" not in used or "requests.memory" not in used:
        raise EnvelopeError("quota status is missing requests.cpu or requests.memory")
    observed = Resources(parse_cpu(used["requests.cpu"]), parse_memory(used["requests.memory"]))
    match = abs(computed.cpu_m - observed.cpu_m) <= tolerance_m and abs(computed.memory - observed.memory) <= tolerance_bytes
    return {"computed": computed, "used": observed, "match": match}


def worker_job_resources(memory_mib: int, cpu: str) -> Resources:
    """Build the equal request/limit resources for a worker Job."""
    if not isinstance(memory_mib, int) or isinstance(memory_mib, bool) or memory_mib < 0:
        raise EnvelopeError("worker memory MiB must be a non-negative integer")
    return Resources(parse_cpu(cpu), memory_mib * 1024**2)
