"""Per-component usage from the existing measured-window cgroup samples."""

from __future__ import annotations

from cluster import role_for_pod


def summarize(intervals: list[dict], started: float, window: tuple[float, float]) -> dict:
    start, end = (started + value for value in window)
    if end <= start:
        raise ValueError("resource window must have positive duration")
    roles: dict[str, dict] = {}
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
    return {"window_seconds": end - start, "roles": roles,
            "limits": "sampled lower-bound peaks; missing/new/replaced pods can reduce coverage; role peaks are not additive"}
