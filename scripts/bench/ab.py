"""A/B comparison of two API images at one fixed arrival rate (methodology §6–§7).

Each arm starts from a reset state (fresh namespaces, reseeded through the
API), measures an idle window, then one open-loop step at the same rate,
seed and mix. Arms run in a counterbalanced order (A B B A A B by default);
comparisons use per-arm means with Student-t 95 % intervals, and a
difference is claimed only when the intervals do not overlap.
"""

from __future__ import annotations

import math
import statistics
import time
from statistics import NormalDist

import deploy
import search
import stats
from cluster import Cluster

IDLE_SETTLE_S = 90
IDLE_WINDOW_S = 120
# Student-t 97.5 % quantiles for small samples (df = n - 1).
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}


def interval(values: list[float]) -> dict:
    """Mean and two-sided 95 % Student-t interval; a single value has no interval."""
    mean = statistics.fmean(values)
    if len(values) < 2:
        return {"mean": mean, "low": None, "high": None, "n": len(values)}
    df = len(values) - 1
    half = _T975.get(df, NormalDist().inv_cdf(0.975)) * statistics.stdev(values) / math.sqrt(len(values))
    return {"mean": mean, "low": mean - half, "high": mean + half, "n": len(values)}


def differs(a: dict, b: dict) -> bool:
    """Whether two intervals are disjoint (both must have one)."""
    if None in (a["low"], b["low"]):
        return False
    return a["high"] < b["low"] or b["high"] < a["low"]


def api_cpu_seconds(step: dict) -> float:
    """CPU-seconds spent by API pods during the step's measured window."""
    start, end = step["started"] + step["window"][0], step["started"] + step["window"][1]
    return sum(
        pod["cpu_cores"] * interval_["seconds"]
        for interval_ in step["host"] if start < interval_["at"] <= end
        for name, pod in interval_["pods"].items() if name.split("/")[1].startswith("bifrost-api-")
    )


def arm(cluster: Cluster, profile: dict, plan: dict, api_image: str, rate: float, prepare) -> dict:
    """One arm: reset, deploy ``api_image``, seed, idle window, then one fixed-rate step."""
    deploy.reset(cluster)
    ids = prepare(api_image)
    time.sleep(IDLE_SETTLE_S)
    before, started = cluster.db_counters(), time.monotonic()
    time.sleep(IDLE_WINDOW_S)
    idle = stats.db_rate(stats.db_delta(before, cluster.db_counters(), time.monotonic() - started))
    before = cluster.db_counters()
    step = search.run_step(cluster, ids, rate, plan, plan["seed"], profile["capacity"]["disk"], window_s=plan["ab_window_s"])
    window = stats.db_delta(before, cluster.db_counters(), 0.0)
    correct = sum(1 for record in step["records"] if record["outcome"] == "correct")
    per_class = {
        name: gates["e2e_p95"]["value"] for name, gates in step["verdict"]["latency"].items()
    }
    return {
        "api_image": api_image,
        "idle": idle,
        "step": step,
        "correct": correct,
        "xact_per_correct": window["xact"] / correct if correct else math.inf,
        "rollbacks_per_correct": window["rollbacks"] / correct if correct else math.inf,
        "api_cpu_s_per_correct": api_cpu_seconds(step) / correct if correct else math.inf,
        "e2e_p95_s": per_class,
        "overall": step["overall"],
    }


METRICS = (
    ("idle transactions/s", lambda a: a["idle"]["xact_per_s"]),
    ("idle rollbacks/s", lambda a: a["idle"]["rollbacks_per_s"]),
    ("transactions per correct run", lambda a: a["xact_per_correct"]),
    ("rollbacks per correct run", lambda a: a["rollbacks_per_correct"]),
    ("API CPU ms per correct run", lambda a: a["api_cpu_s_per_correct"] * 1000),
)


def compare(arms: list[dict], images: dict[str, str]) -> list[dict]:
    """Per-metric intervals for each variant, and whether they differ."""
    rows = []
    for label, metric in METRICS:
        values = {variant: [metric(a) for a in arms if a["api_image"] == image] for variant, image in images.items()}
        finite = {variant: [v for v in vs if math.isfinite(v)] for variant, vs in values.items()}
        intervals = {variant: interval(vs) for variant, vs in finite.items() if vs}
        rows.append({
            "metric": label,
            **intervals,
            "differs": len(intervals) == 2 and differs(intervals["a"], intervals["b"]),
            "non_finite": {variant: len(values[variant]) - len(finite[variant]) for variant in values},
        })
    classes = sorted({name for a in arms for name in a["e2e_p95_s"]})
    for name in classes:
        values = {variant: [a["e2e_p95_s"].get(name) for a in arms if a["api_image"] == image] for variant, image in images.items()}
        infinite = {variant: sum(1 for v in vs if v is None or not math.isfinite(v)) for variant, vs in values.items()}
        if any(infinite.values()):
            rows.append({"metric": f"{name} e2e p95 s", "infinite_in": infinite})
            continue
        intervals = {variant: interval(vs) for variant, vs in values.items() if vs}
        rows.append({"metric": f"{name} e2e p95 s", **intervals,
                     "differs": len(intervals) == 2 and differs(intervals["a"], intervals["b"])})
    gates = {variant: sum(1 for a in arms if a["api_image"] == image and a["overall"] == "pass") for variant, image in images.items()}
    counts = {variant: sum(1 for a in arms if a["api_image"] == image) for variant, image in images.items()}
    rows.append({"metric": "steps passing all gates", "a": f"{gates['a']}/{counts['a']}", "b": f"{gates['b']}/{counts['b']}"})
    return rows
