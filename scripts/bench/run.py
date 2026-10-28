#!/usr/bin/env python3
"""Bifrost benchmark harness (stdlib only).

    ./test.sh bench up
    ./test.sh bench run --profile baseline [--backend fake|deepseek|none] [--out DIR]
    ./test.sh bench down

``up`` boots the worktree's Kind cluster on the production image
(``scripts/kubernetes/local-kind.sh``) and adds the fake model and driver pod.
``run`` shapes the stack from the profile, seeds it, times each execution
class twice, snapshots API and worker memory after every step, and writes
``result.json`` and ``report.md``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import report  # noqa: E402
import smaps  # noqa: E402
import stats  # noqa: E402
from cluster import ROOT, Cluster  # noqa: E402

KIND = ROOT / "scripts" / "kubernetes" / "local-kind.sh"
DRIVER = HERE / "incluster" / "driver.py"


def _git_sha() -> str:
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    return f"{sha}{'-dirty' if dirty else ''}"


def _image_id(image: str) -> str:
    return subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True).stdout.strip()


def apply_bench_resources(cluster: Cluster) -> None:
    configmap = cluster.kubectl(
        "create", "configmap", "bench-scripts", f"--from-file={HERE / 'incluster'}", "--dry-run=client", "-o", "yaml",
    )
    cluster.kubectl("apply", "-f", "-", input=configmap)
    manifest = (HERE / "manifests" / "bench.yaml").read_text(encoding="utf-8").replace("BENCH_IMAGE", cluster.image)
    cluster.kubectl("delete", "pod", "bench-driver", "--ignore-not-found", "--wait=true")
    cluster.kubectl("apply", "-f", "-", input=manifest)
    cluster.kubectl("rollout", "restart", "deployment/bench-fake-llm")
    cluster.kubectl("rollout", "status", "deployment/bench-fake-llm", "--timeout=180s")
    cluster.kubectl("wait", "--for=condition=Ready", "pod/bench-driver", "--timeout=180s")


def store_model_key(cluster: Cluster, onepassword_profile: str, onepassword_item: str) -> None:
    """Copy the DeepSeek key from 1Password into the cluster without touching disk or argv.

    Uses the ``op-<profile>`` wrapper (see the 1password-secrets skill), which
    exposes the service-account token only to its own ``op`` child.
    """
    read = subprocess.run([f"op-{onepassword_profile}", "read", onepassword_item], capture_output=True, text=True)
    if read.returncode != 0 or not read.stdout.strip():
        raise SystemExit(f"Could not read {onepassword_item} from 1Password: {read.stderr.strip()}")
    secret = json.dumps({
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": "bench-llm", "namespace": cluster.namespace},
        "type": "Opaque", "stringData": {"DEEPSEEK_API_KEY": read.stdout.strip()},
    })
    cluster.kubectl("apply", "-f", "-", input=secret)


SAMPLE_INTERVAL_S = 0.5


class WorkerSampler:
    """Samples worker process memory twice a second while a class runs.

    Runs shorter than the interval can be missed; the peaks are a lower bound.
    """

    def __init__(self, cluster: Cluster) -> None:
        self.cluster = cluster
        self.samples: list[dict[str, list[dict]]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self) -> "WorkerSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            sample: dict[str, list[dict]] = {}
            for pod in self.cluster.worker_pods():
                try:
                    sample[pod] = self.cluster.worker_processes(pod)
                except RuntimeError:
                    continue  # the Job finished between listing and exec
            self.samples.append(sample)
            self._stop.wait(max(0.0, SAMPLE_INTERVAL_S - (time.monotonic() - started)))


class Run:
    def __init__(self, cluster: Cluster, profile_name: str, profile: dict, backend: str | None, out: Path) -> None:
        self.cluster = cluster
        self.out = out
        self.expected_api_uid: str | None = None
        self.result = {
            "run_id": out.name,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_sha": _git_sha(),
            "image_id": _image_id(cluster.image),
            "profile_name": profile_name,
            "profile": profile,
            "backend": backend,
            "api_pod_changes": 0,
            "setup": None,
            "classes": [],
            "snapshots": [],
        }

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "result.json").write_text(json.dumps(self.result, indent=2), encoding="utf-8")
        (self.out / "report.md").write_text(report.render(self.result), encoding="utf-8")

    def snapshot(self, label: str) -> None:
        pod, uid = self.cluster.api_pod()
        if self.expected_api_uid is not None and uid != self.expected_api_uid:
            self.result["api_pod_changes"] += 1
            self.save()
            raise SystemExit(f"API pod was replaced during the run (before '{label}'); see {self.out}")
        self.expected_api_uid = uid
        summary = smaps.summarize(self.cluster.smaps(pod, "api"))
        db = self.cluster.db_counters()
        region = summary.regions.get

        def mib(name: str, field: str) -> float:
            found = region(name)
            return round(getattr(found, field) / 1024, 1) if found else 0.0

        self.result["snapshots"].append({
            "label": label,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "api_pod": pod,
            "api": {
                "rss_mib": round(summary.rss_kib / 1024, 1),
                "pss_mib": round(summary.pss_kib / 1024, 1),
                "thp_mib": round(summary.thp_kib / 1024, 1),
                "heap_virt_mib": mib("heap", "virtual_kib"),
                "heap_rss_mib": mib("heap", "rss_kib"),
                "anon_large_rss_mib": mib("anon_large", "rss_kib"),
                "file_rss_mib": mib("file", "rss_kib"),
            },
            "containers": self.cluster.container_stats(),
            "db_mib": round(db["db_bytes"] / 1024 / 1024, 1),
            "wal_mib": round(db["wal_bytes"] / 1024 / 1024, 1),
        })
        self.save()
        api = self.result["snapshots"][-1]["api"]
        print(f"  [{label}] API {api['rss_mib']:.0f} MiB (heap {api['heap_rss_mib']:.0f})", flush=True)

    def driver(self, mode: str, args: dict, timeout_s: int) -> dict:
        return self.cluster.exec_python("bench-driver", DRIVER, [mode, json.dumps(args)], timeout_s=timeout_s)


def run(profile_name: str, backend: str | None, out: Path | None) -> Path:
    profile = json.loads((HERE / "profiles" / f"{profile_name}.json").read_text(encoding="utf-8"))
    cluster = Cluster()
    if backend == "deepseek":
        store_model_key(cluster, profile["onepassword_profile"], profile["onepassword_item"])
    out = out or Path(f"/tmp/bifrost-bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{profile_name}-{backend or 'none'}")
    print(f"Shaping the stack for '{profile_name}' ...", flush=True)
    cluster.apply_profile(profile)
    apply_bench_resources(cluster)
    cluster.restart_api()
    time.sleep(profile["settle_s"])
    current = Run(cluster, profile_name, profile, backend, out)
    current.snapshot("fresh")
    print("Seeding (idempotent) ...", flush=True)
    current.result["setup"] = current.driver("setup", {
        "seed": profile["seed"], "backend": backend, "policy": profile["worker_policy"], "table_docs": profile["table_docs"],
    }, timeout_s=3600)
    cluster.restart_api()
    current.expected_api_uid = None
    time.sleep(profile["settle_s"])
    current.snapshot("boot_seeded")
    classes = [c for c in profile["classes"] if backend or not c.startswith(("agent-", "chat-"))]
    for pass_number in (1, 2):
        for name in classes:
            print(f"Pass {pass_number}: {name} ...", flush=True)
            cpu_before = cluster.container_stats()
            db_before, started = cluster.db_counters(), time.monotonic()
            with WorkerSampler(cluster) as sampler:
                records = current.driver("run_class", {
                    "name": name, "sequential": profile["sequential"], "burst": profile["burst"],
                    "burst_rounds": profile["burst_rounds"], "timeout_s": profile["timeout_s"],
                }, timeout_s=profile["timeout_s"] * (profile["sequential"] + profile["burst_rounds"] + 1))
            time.sleep(profile["settle_s"])
            runs = len(records["sequential"]) + len(records["burst"])
            db_window = stats.db_delta(db_before, cluster.db_counters(), time.monotonic() - started)
            current.result["classes"].append({
                "name": name,
                "pass": pass_number,
                "sequential": stats.timings(records["sequential"]),
                "burst": stats.timings(records["burst"]),
                "cpu_ms_per_run": stats.cpu_ms_per_run(cpu_before, cluster.container_stats(), runs),
                "runs": runs,
                "db_window": db_window,
                "db_top_tables": stats.top_tables(db_window["tables"], 8),
                "worker_children": stats.worker_children(sampler.samples),
                "raw": records,
            })
            current.snapshot(f"{name} (pass {pass_number})")
    print(f"Idle {profile['idle_s']} s, measuring database load over the last {profile['idle_db_s']} s ...", flush=True)
    time.sleep(profile["idle_s"] - profile["idle_db_s"])
    db_before, started = cluster.db_counters(), time.monotonic()
    time.sleep(profile["idle_db_s"])
    idle_window = stats.db_delta(db_before, cluster.db_counters(), time.monotonic() - started)
    idle_rate = stats.db_rate(idle_window)
    current.result["db_idle"] = {
        "seconds": round(idle_window["seconds"], 1),
        "rate": idle_rate,
        "top_tables": stats.top_tables(idle_window["tables"], 8),
    }
    # Net each class of the steady idle rate, measured once nothing else is running.
    for entry in current.result["classes"]:
        entry["db_per_run"] = stats.db_per_run(entry["db_window"], idle_rate, entry["runs"])
    print(f"  idle: {idle_rate['xact_per_s']:.1f} transactions/s", flush=True)
    current.snapshot("idle")
    print(f"Done: {out}/report.md", flush=True)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("up")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--profile", default="baseline")
    run_parser.add_argument("--backend", choices=["fake", "deepseek", "none"], default="fake")
    run_parser.add_argument("--out", type=Path)
    sub.add_parser("down")
    args = parser.parse_args()

    if args.command == "up":
        subprocess.run([str(KIND), "up"], check=True)
        apply_bench_resources(Cluster())
    elif args.command == "run":
        run(args.profile, None if args.backend == "none" else args.backend, args.out)
    elif args.command == "down":
        cluster = Cluster()
        subprocess.run([str(KIND), "down"], check=True)
        subprocess.run(["docker", "image", "rm", cluster.image], check=False)


if __name__ == "__main__":
    main()
