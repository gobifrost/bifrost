#!/usr/bin/env python3
"""Bifrost benchmark harness (stdlib only).

    ./test.sh bench up [--profile m]
    ./test.sh bench run --profile m [--backend fake|none] [--out DIR]
    ./test.sh bench capacity --profile m --mix sdk-heavy [--out DIR]
    ./test.sh bench down

``up`` creates the worktree's own Kind cluster (fail-closed guard, sentinel),
loads the production API and client images, installs the pinned CloudNativePG
operator, proves egress isolation, and deploys the tenant inside its resource
envelope. ``run`` times each execution class sequentially and in bursts and
snapshots memory and database counters. ``capacity`` searches for the
sustainable arrival rate of one workload mix. Methodology:
docs/plans/2026-10-29-capacity-benchmark-methodology.md.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import deploy  # noqa: E402
import guard  # noqa: E402
import report  # noqa: E402
import search  # noqa: E402
import smaps  # noqa: E402
import stats  # noqa: E402
from cluster import ROOT, Cluster  # noqa: E402

KIND = ROOT / "scripts" / "kubernetes" / "local-kind.sh"
KIND_VERSION = "v0.30.0"
DRIVER = HERE / "incluster" / "driver.py"
LOADGEN = HERE / "incluster" / "loadgen.py"


def load_profile(name: str) -> dict:
    """A profile file, merged over the profile it ``extends``."""
    profile = json.loads((HERE / "profiles" / f"{name}.json").read_text(encoding="utf-8"))
    parent = profile.pop("extends", None)
    return {**load_profile(parent), **profile} if parent else profile


def _git_sha() -> str:
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    return f"{sha}{'-dirty' if dirty else ''}"


def _image_id(image: str) -> str:
    return subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True).stdout.strip()


def environment(cluster: Cluster) -> dict:
    """Build and cluster facts every result records."""
    images = deploy.images(cluster.identity)
    return {
        "git_sha": _git_sha(),
        "images": {name: {"tag": tag, "id": _image_id(tag)} for name, tag in images.items()},
        "kind_version": KIND_VERSION,
        "cnpg_version": deploy.CNPG_VERSION,
        "host": {"cpus": os.cpu_count(), "load_average": os.getloadavg()},
        "external_nodes": cluster.external_nodes(),
    }


def up(profile: dict) -> None:
    identity = guard.expected_identity(ROOT)
    guard.check_creation(os.environ.get("DOCKER_HOST"), identity.cluster_name, identity)
    existing = subprocess.run(["docker", "inspect", identity.node_name], capture_output=True).returncode == 0
    if existing and not identity.sentinel_path.exists():
        raise SystemExit(f"{identity.cluster_name} exists without this harness's sentinel; run ./test.sh bench down first")
    tags = deploy.build_images(identity)
    subprocess.run([str(KIND), "cluster", tags["client"]], check=True, env={**os.environ, "BIFROST_KIND_VERSION": KIND_VERSION})
    cluster = Cluster()
    cluster.write_sentinel()
    cluster.verify()
    deploy.install_cnpg(cluster, identity)
    print(json.dumps(deploy.apply(cluster, profile), indent=2), flush=True)


def check_destinations(cluster: Cluster, ids: dict) -> None:
    """Every outbound destination the tenant and driver can use must be a benchmark Service."""
    config = json.loads(cluster.kubectl("get", "configmap", "bifrost-config", "-o", "json"))["data"]
    urls = {key: value for key, value in config.items() if key.endswith(("_URL", "_ENDPOINT_URL"))}
    secret = json.loads(cluster.kubectl("get", "secret", "bifrost-secrets", "-o", "json"))["data"]
    for key in ("BIFROST_DATABASE_URL", "BIFROST_DATABASE_URL_SYNC"):
        urls[key] = subprocess.run(["base64", "-d"], input=secret[key], capture_output=True, text=True, check=True).stdout
    guard.check_destinations(urls, guard.TENANT_NS)
    guard.check_destinations({"BENCH_API_URL": "http://api.bench-tenant:8000"}, guard.INFRA_NS)
    defaulted = [name for name, endpoint in ids["connections"].items() if not endpoint]
    if defaulted:
        raise guard.GuardError(f"AI connections without an endpoint use a public provider default: {defaulted}")
    guard.check_destinations({f"ai:{name}": endpoint for name, endpoint in ids["connections"].items()}, guard.TENANT_NS)


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

    def __enter__(self) -> WorkerSampler:
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
    def __init__(self, cluster: Cluster, kind: str, profile_name: str, profile: dict, backend: str | None, out: Path) -> None:
        self.cluster = cluster
        self.out = out
        self.expected_api_uid: str | None = None
        self.result: dict = {
            "schema_version": 2,
            "kind": kind,
            "run_id": out.name,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **environment(cluster),
            "profile_name": profile_name,
            "profile": profile,
            "backend": backend,
            "api_pod_changes": 0,
            "deploy": None,
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
        print(f"  [{label}] API {api['rss_mib']:.0f} MiB", flush=True)

    def driver(self, mode: str, args: dict, timeout_s: int) -> dict:
        return self.cluster.exec_python("bench-driver", DRIVER, [mode, json.dumps(args)], timeout_s=timeout_s)


def _prepare(cluster: Cluster, current: Run, profile: dict, backend: str | None) -> dict:
    """Deploy the profile, seed through the API, reboot the API on the seeded data, check destinations."""
    current.result["deploy"] = deploy.apply(cluster, profile)
    current.result["setup"] = current.driver("setup", {
        "seed": profile["seed"], "backend": backend, "policy": profile["worker_policy"], "table_docs": profile["table_docs"],
    }, timeout_s=3600)
    ids = cluster.exec_python("bench-driver", LOADGEN, ["prepare", "{}"], timeout_s=900)
    check_destinations(cluster, ids)
    cluster.restart_api()
    current.expected_api_uid = None
    time.sleep(profile["settle_s"])
    return ids


def run_classes(profile_name: str, backend: str | None, out: Path | None) -> Path:
    profile = load_profile(profile_name)
    cluster = Cluster()
    cluster.verify()
    out = out or Path(f"/tmp/bifrost-bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{profile_name}-{backend or 'none'}")
    current = Run(cluster, "classes", profile_name, profile, backend, out)
    print(f"Deploying and seeding '{profile_name}' ...", flush=True)
    _prepare(cluster, current, profile, backend)
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


def run_capacity(profile_name: str, mix_name: str, out: Path | None) -> Path:
    profile = load_profile(profile_name)
    plan = {**profile["capacity"], "mix": profile["capacity"]["mixes"][mix_name], "start_rate": profile["capacity"]["start_rates"][mix_name]}
    cluster = Cluster()
    cluster.verify()
    out = out or Path(f"/tmp/bifrost-bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{profile_name}-capacity-{mix_name}")
    current = Run(cluster, "capacity", profile_name, profile, "fake", out)
    current.result["mix_name"] = mix_name
    print(f"Deploying and seeding '{profile_name}' ...", flush=True)
    ids = _prepare(cluster, current, profile, "fake")
    current.result["ids"] = ids
    current.result["steps"] = []

    def on_step(step: dict) -> None:
        current.result["steps"].append(step)
        current.save()
        print(f"  {step['rate']:.3f}/s for {step['window_s']:.0f} s: {step['overall']}"
              + (f" ({'; '.join(step['validity']['invalid'])})" if step["validity"]["invalid"] else ""), flush=True)

    result = search.search(cluster, ids, plan, profile["capacity"]["disk"], on_step)
    current.result["bracket"] = result["bracket"]
    current.result["stopped"] = result["stopped"]
    current.save()
    print(f"Bracket {result['bracket']}; done: {out}/report.md", flush=True)
    return out


def down() -> None:
    cluster = Cluster()
    cluster.verify()
    subprocess.run([str(KIND), "down"], check=True, env={**os.environ, "BIFROST_KIND_VERSION": KIND_VERSION})
    cluster.identity.sentinel_path.unlink(missing_ok=True)
    for tag in deploy.images(cluster.identity).values():
        subprocess.run(["docker", "image", "rm", tag], check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    up_parser = sub.add_parser("up")
    up_parser.add_argument("--profile", default="m")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--profile", default="m")
    run_parser.add_argument("--backend", choices=["fake", "none"], default="fake")
    run_parser.add_argument("--out", type=Path)
    capacity_parser = sub.add_parser("capacity")
    capacity_parser.add_argument("--profile", default="m")
    capacity_parser.add_argument("--mix", required=True)
    capacity_parser.add_argument("--out", type=Path)
    sub.add_parser("down")
    args = parser.parse_args()

    if args.command == "up":
        up(load_profile(args.profile))
    elif args.command == "run":
        run_classes(args.profile, None if args.backend == "none" else args.backend, args.out)
    elif args.command == "capacity":
        run_capacity(args.profile, args.mix, args.out)
    elif args.command == "down":
        down()


if __name__ == "__main__":
    main()
