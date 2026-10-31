"""Per-component usage from the existing measured-window cgroup samples."""

from __future__ import annotations

from cluster import role_for_pod


def _memory(values: dict) -> tuple[int, int] | None:
    memory = values.get("memory_bytes")
    if not isinstance(memory, dict):
        return None
    current, working_set = memory.get("current"), memory.get("working_set")
    if not isinstance(current, int) or not isinstance(working_set, int):
        return None
    return current, working_set


def summarize(intervals: list[dict], started: float, window: tuple[float, float]) -> dict:
    start, end = (started + value for value in window)
    if end <= start:
        raise ValueError("resource window must have positive duration")
    roles: dict[str, dict] = {}
    tenant = {
        "kind": "observed_tenant_lower_bound",
        "observed_seconds": 0.0,
        "cpu_seconds": 0.0,
        "mean_cpu_cores_observed": None,
        "max_interval_cpu_cores": None,
        "charged_peak_bytes": None,
        "working_set_peak_bytes": None,
        "observed_uid_scoped_pods": set(),
        "observed_samples": 0,
        "memory_complete_samples": 0,
        "memory_missing_or_partial_samples": 0,
        "memory_observed_seconds": 0.0,
    }
    for sample in intervals:
        seconds = sample["seconds"]
        # CPU is an interval delta; do not charge a warm-up interval to the
        # measured phase merely because its final timestamp falls inside it.
        if seconds <= 0 or sample["at"] - seconds < start or sample["at"] > end:
            continue
        grouped: dict[str, list[tuple[str, dict]]] = {}
        for name, values in sample["pods"].items():
            if name.startswith("bench-tenant/"):
                grouped.setdefault(role_for_pod(name.split("/", 1)[1]), []).append((name, values))
        tenant_pods = [pod for pods in grouped.values() for pod in pods]
        if tenant_pods:
            cpu = sum(values["cpu_cores"] for _, values in tenant_pods)
            tenant["observed_seconds"] += seconds
            tenant["cpu_seconds"] += cpu * seconds
            tenant["max_interval_cpu_cores"] = (
                cpu if tenant["max_interval_cpu_cores"] is None else max(tenant["max_interval_cpu_cores"], cpu)
            )
            tenant["observed_uid_scoped_pods"].update(name for name, _ in tenant_pods if "@" in name)
            tenant["observed_samples"] += 1
            memory = [_memory(values) for _, values in tenant_pods]
            if all(values is not None for values in memory):
                charged, working_set = (sum(values[index] for values in memory) for index in (0, 1))
                tenant["charged_peak_bytes"] = charged if tenant["charged_peak_bytes"] is None else max(tenant["charged_peak_bytes"], charged)
                tenant["working_set_peak_bytes"] = working_set if tenant["working_set_peak_bytes"] is None else max(tenant["working_set_peak_bytes"], working_set)
                tenant["memory_complete_samples"] += 1
                tenant["memory_observed_seconds"] += seconds
            else:
                tenant["memory_missing_or_partial_samples"] += 1
        for role, pods in grouped.items():
            row = roles.setdefault(role, {"observed_seconds": 0.0, "cpu_seconds": 0.0, "max_interval_cpu_cores": 0.0,
                                          "max_pod_throttled_ratio": 0.0, "charged_peak_bytes": None,
                                          "working_set_peak_bytes": None, "observed_pods": set(), "samples": 0})
            cpu = sum(values["cpu_cores"] for _, values in pods)
            row["observed_seconds"] += seconds
            row["cpu_seconds"] += cpu * seconds
            row["max_interval_cpu_cores"] = max(row["max_interval_cpu_cores"], cpu)
            row["max_pod_throttled_ratio"] = max(row["max_pod_throttled_ratio"], *(values["throttled_ratio"] for _, values in pods))
            row["observed_pods"].update(name for name, _ in pods)
            row["samples"] += 1
            if all("memory_bytes" in values for _, values in pods):
                for target, metric in (("charged_peak_bytes", "current"), ("working_set_peak_bytes", "working_set")):
                    value = sum(values["memory_bytes"][metric] for _, values in pods)
                    row[target] = value if row[target] is None else max(row[target], value)
    for row in roles.values():
        row["mean_cpu_cores_observed"] = row["cpu_seconds"] / row["observed_seconds"]
        row["observed_window_fraction"] = row["observed_seconds"] / (end - start)
        row["observed_pods"] = sorted(row["observed_pods"])
    tenant["observed_window_fraction"] = tenant["observed_seconds"] / (end - start)
    memory_observed_seconds = tenant.pop("memory_observed_seconds")
    tenant["memory_coverage"] = {
        "observed_seconds": memory_observed_seconds,
        "observed_window_fraction": memory_observed_seconds / (end - start),
        "complete_observed_samples": tenant.pop("memory_complete_samples"),
        "missing_or_partial_observed_samples": tenant.pop("memory_missing_or_partial_samples"),
    }
    if tenant["observed_seconds"]:
        tenant["mean_cpu_cores_observed"] = tenant["cpu_seconds"] / tenant["observed_seconds"]
    tenant["observed_uid_scoped_pods"] = sorted(tenant["observed_uid_scoped_pods"])
    return {"window_seconds": end - start, "roles": roles, "tenant": tenant,
            "limits": "sampled lower-bound peaks; missing/new/replaced or unsampled pods reduce coverage; role peaks are not additive and tenant is not a complete namespace inventory"}
