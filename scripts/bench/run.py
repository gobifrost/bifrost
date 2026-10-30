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
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ab  # noqa: E402
import deploy  # noqa: E402
import drills  # noqa: E402
import guard  # noqa: E402
import import_diagnostic  # noqa: E402
import jsoncodec  # noqa: E402
import preflight  # noqa: E402
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


def _image_record(image: str, *, required: bool = False) -> dict[str, str]:
    """The local immutable image ID, failing before deploy when an image was selected."""
    image_id = _image_id(image)
    if required and not image_id:
        raise SystemExit(f"selected API image is not available locally: {image}")
    return {"tag": image, "id": image_id}


def environment(cluster: Cluster, api_image: str | None = None) -> dict:
    """Build and cluster facts every result records."""
    images = deploy.images(cluster.identity)
    if api_image is not None:
        images["api"] = api_image
    return {
        "git_sha": _git_sha(),
        "images": {name: _image_record(tag, required=name == "api" and api_image is not None) for name, tag in images.items()},
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


def _atomic_write(path: Path, content: str) -> None:
    """Replace a result artifact only after its complete new content reaches disk."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class Run:
    def __init__(self, cluster: Cluster, kind: str, profile_name: str, profile: dict, backend: str | None, out: Path,
                 api_image: str | None = None) -> None:
        self.cluster = cluster
        self.out = out
        self.expected_api_uid: str | None = None
        self.result: dict = {
            "schema_version": jsoncodec.SCHEMA_VERSION,
            "kind": kind,
            "run_id": out.name,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **environment(cluster, api_image),
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
        _atomic_write(self.out / "result.json", json.dumps(jsoncodec.encode(self.result), indent=2, allow_nan=False))
        _atomic_write(self.out / "report.md", report.render(self.result))

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
            "worker_processes": {
                pod: self.cluster.worker_processes(pod)
                for pod in self.cluster.worker_pods()
            },
            "worker_images": self.cluster.worker_image_evidence(self.result["api_image_provenance"])
            if self.result.get("api_image_provenance") else {},
            "db_mib": round(db["db_bytes"] / 1024 / 1024, 1),
            "wal_mib": round(db["wal_bytes"] / 1024 / 1024, 1),
        })
        self.save()
        api = self.result["snapshots"][-1]["api"]
        print(f"  [{label}] API {api['rss_mib']:.0f} MiB", flush=True)

    def driver(self, mode: str, args: dict, timeout_s: int) -> dict:
        return self.cluster.exec_python("bench-driver", DRIVER, [mode, json.dumps(args)], timeout_s=timeout_s)


def _prepare(cluster: Cluster, current: Run, profile: dict, backend: str | None, api_image: str | None = None) -> dict:
    """Deploy the profile, seed through the API, reboot the API on the seeded data, check destinations."""
    selected_api_image = api_image or deploy.images(cluster.identity)["api"]
    host_id = _image_record(selected_api_image, required=True)["id"]
    current.result["api_image_provenance"] = cluster.image_provenance(selected_api_image, host_id)
    current.save()
    current.result["deploy"] = deploy.apply(cluster, profile, selected_api_image)
    current.result["setup"] = current.driver("setup", {
        "seed": profile["seed"], "backend": backend, "policy": profile["worker_policy"], "table_docs": profile["table_docs"],
        "import_fixture": import_diagnostic.fixture_manifest() if profile.get("imports_diagnostic") else None,
    }, timeout_s=3600)
    ids = cluster.exec_python("bench-driver", LOADGEN, ["prepare", "{}"], timeout_s=900)
    check_destinations(cluster, ids)
    cluster.restart_api()
    current.result["api_image_provenance"]["api_pod"] = cluster.api_image_evidence(current.result["api_image_provenance"])
    current.save()
    current.expected_api_uid = None
    time.sleep(profile["settle_s"])
    return ids


def run_classes(profile_name: str, backend: str | None, out: Path | None, api_image: str | None = None) -> Path:
    profile = load_profile(profile_name)
    out = out or Path(f"/tmp/bifrost-bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{profile_name}-{backend or 'none'}")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SystemExit(f"classes output already contains evidence: {out}")
    cluster = Cluster()
    cluster.verify()
    current = Run(cluster, "classes", profile_name, profile, backend, out, api_image)
    print(f"Deploying and seeding '{profile_name}' ...", flush=True)
    _prepare(cluster, current, profile, backend, api_image)
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
            raw_records = [*records["sequential"], *records["burst"]]
            entry = {
                "name": name,
                "pass": pass_number,
                "sequential": stats.timings(records["sequential"]),
                "burst": stats.timings(records["burst"]),
                "runs": len(raw_records),
                "worker_children": stats.worker_children(sampler.samples),
                "raw": records,
            }
            imports_diagnostic = profile.get("imports_diagnostic")
            if imports_diagnostic:
                # Save raw terminal records before log capture, settling, or counter reads.
                current.result["classes"].append(entry)
                current.save()
                execution = import_diagnostic.validate_execution_records(raw_records)
                try:
                    trace = import_diagnostic.capture(
                        cluster, current.out, name, pass_number, [record["id"] for record in raw_records],
                    )
                except Exception as error:
                    trace = {
                        "kind": "partial-imports-diagnostic", "overall": "invalid",
                        "expected_execution_ids": [record["id"] for record in raw_records],
                        "sources": [], "events": [], "missing_execution_ids": [],
                        "duplicate_execution_ids": [], "unmatched_execution_ids": [], "invalid_events": [],
                        "capture_error": f"{type(error).__name__}: {error}",
                    }
                entry["import_trace"] = trace
                entry["import_execution_validation"] = execution
                entry["worker_process_samples"] = sampler.samples
                diagnostic = current.result.setdefault(
                    "imports_diagnostic", {"kind": "partial-imports-diagnostic", "overall": "pass", "groups": []},
                )
                group = {
                    "class": name, "pass": pass_number, **trace,
                    "execution_validation": execution,
                    "durable_timing": import_diagnostic.durable_timing(raw_records),
                }
                diagnostic["groups"].append(group)
                if trace["overall"] != "pass" or execution["overall"] != "pass":
                    diagnostic["overall"] = "invalid"
                current.save()
            time.sleep(profile["settle_s"])
            runs = entry["runs"]
            db_window = stats.db_delta(db_before, cluster.db_counters(), time.monotonic() - started)
            entry.update({
                "cpu_ms_per_run": stats.cpu_ms_per_run(cpu_before, cluster.container_stats(), runs),
                "db_window": db_window,
                "db_top_tables": stats.top_tables(db_window["tables"], 8),
            })
            if not imports_diagnostic:
                current.result["classes"].append(entry)
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


def run_capacity(profile_name: str, mix_name: str, api_image: str | None, out: Path | None) -> Path:
    profile = load_profile(profile_name)
    plan = {**profile["capacity"], "mix": profile["capacity"]["mixes"][mix_name], "start_rate": profile["capacity"]["start_rates"][mix_name]}
    cluster = Cluster()
    cluster.verify()
    out = out or Path(f"/tmp/bifrost-bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{profile_name}-capacity-{mix_name}")
    selected_api_image = api_image or deploy.images(cluster.identity)["api"]
    current = Run(cluster, "capacity", profile_name, profile, "fake", out, selected_api_image)
    current.result.update({"mix_name": mix_name, "api_image": selected_api_image})
    current.result["steps"] = []
    current.result["confirmations"] = []

    def fresh() -> dict:
        print(f"Resetting and seeding '{profile_name}' ...", flush=True)
        deploy.reset(cluster)
        ids = _prepare(cluster, current, profile, "fake", selected_api_image)
        current.result["ids"] = ids
        return ids

    def on_step(step: dict) -> None:
        current.result["steps"].append(step)
        current.save()
        print(f"  {step['rate']:.3f}/s for {step['window_s']:.0f} s: {step['overall']}"
              + (f" ({'; '.join(step['validity']['invalid'])})" if step["validity"]["invalid"] else ""), flush=True)

    result = search.search(cluster, fresh(), plan, profile["capacity"]["disk"], on_step)
    current.result["bracket"] = result["bracket"]
    current.result["stopped"] = result["stopped"]
    current.save()
    passed = sorted({step["rate"] for step in result["steps"] if step["overall"] == "pass"}, reverse=True)
    current.result["sustainable_rate"] = None
    for rate in passed:
        print(f"Confirming {rate:.3f}/s from a reset state ...", flush=True)
        step = search.confirm(cluster, fresh(), rate, plan, profile["capacity"]["disk"])
        current.result["confirmations"].append(step)
        current.save()
        print(f"  confirmation {rate:.3f}/s for {step['window_s']:.0f} s: {step['overall']}", flush=True)
        if step["overall"] == "pass":
            current.result["sustainable_rate"] = rate
            break
    current.save()
    print(f"Bracket {result['bracket']}; sustainable {current.result['sustainable_rate']}; done: {out}/report.md", flush=True)
    return out


def run_confirmation(profile_name: str, mix_name: str, rate: float, api_image: str | None, out: Path | None) -> Path:
    """Confirm one offered rate after a representative correctness preflight and a fresh reset."""
    if not math.isfinite(rate) or rate <= 0:
        raise SystemExit("confirmation rate must be finite and greater than zero")
    out = out or Path(f"/tmp/bifrost-bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{profile_name}-capacity-confirmation-only-{mix_name}")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SystemExit(f"confirmation output already contains evidence: {out}")
    profile = load_profile(profile_name)
    plan = {**profile["capacity"], "mix": profile["capacity"]["mixes"][mix_name]}
    cluster = Cluster()
    cluster.verify()
    selected_api_image = api_image or deploy.images(cluster.identity)["api"]
    current = Run(cluster, "capacity", profile_name, profile, "fake", out, selected_api_image)
    current.result.update({
        "mode": "confirmation-only", "mix_name": mix_name, "rate": rate, "api_image": selected_api_image,
        "steps": [], "confirmations": [], "bracket": None, "stopped": None, "sustainable_rate": None,
        "phase_definition": {
            "first": "30-second representative correctness preflight after the first full reset/seed",
            "steady": "unchanged long confirmation after a second full reset/seed",
        },
    })
    # Preserve the selected image and planned confirmation even when deployment fails.
    current.save()

    def reset_and_prepare(phase: str) -> dict:
        print(f"Resetting and seeding '{profile_name}' for {phase} at {rate:.3f}/s ...", flush=True)
        deploy.reset(cluster)
        ids = _prepare(cluster, current, profile, "fake", selected_api_image)
        current.result[f"{phase}_setup"] = {
            "ids": ids,
            "api_image_provenance": copy.deepcopy(current.result["api_image_provenance"]),
        }
        current.save()
        return ids

    preflight_ids = reset_and_prepare("preflight")

    def save_preflight(progress: dict) -> None:
        current.result["preflight"] = progress
        current.save()

    try:
        candidate = preflight.run(cluster, preflight_ids, rate, plan, current.out / "preflight-logs", save_preflight)
    except BaseException as error:  # save unexpected harness failure before its cleanup decision
        candidate = {
            "kind": "representative-correctness-preflight", "overall": "invalid",
            "error": f"{type(error).__name__}: {error}",
            "requires_namespace_teardown": bool(getattr(error, "requires_namespace_teardown", False)),
        }
    finally:
        current.result["preflight"] = candidate
        current.save()
    if candidate["overall"] != "pass":
        current.result["stopped"] = "preflight-failed"
        current.save()
        try:
            deploy.reset(cluster)
        except BaseException as error:
            candidate["namespace_cleanup"] = {"verified": False, "error": f"{type(error).__name__}: {error}"}
            current.save()
            raise
        candidate["namespace_cleanup"] = {"verified": True}
        current.save()
        print(f"  preflight {candidate['overall']}; long confirmation not run: {out}/report.md", flush=True)
        return out

    ids = reset_and_prepare("confirmation")
    current.result["ids"] = ids
    print(f"Idle {profile['idle_s']} s, measuring database load over the last {profile['idle_db_s']} s ...", flush=True)
    time.sleep(profile["idle_s"] - profile["idle_db_s"])
    idle_before, idle_started = cluster.db_counters(), time.monotonic()
    time.sleep(profile["idle_db_s"])
    idle_window = stats.db_delta(idle_before, cluster.db_counters(), time.monotonic() - idle_started)
    idle_rate = stats.db_rate(idle_window)
    current.result["db_idle"] = {
        "seconds": round(idle_window["seconds"], 1), "rate": idle_rate,
        "top_tables": stats.top_tables(idle_window["tables"], 8),
    }
    current.snapshot("idle")
    phases_before, phases_started = cluster.db_counters(), time.monotonic()
    step = search.confirm(cluster, ids, rate, plan, profile["capacity"]["disk"])
    current.result["all_phases_db_window"] = {
        "label": "warmup+measure+drain",
        **stats.db_delta(phases_before, cluster.db_counters(), time.monotonic() - phases_started),
    }
    current.result["confirmations"].append(step)
    current.result["steady_phase"] = {"started": step["started"], "window": step["window"], "window_s": step["window_s"]}
    if step["overall"] == "pass":
        current.result["sustainable_rate"] = rate
    current.save()
    current.snapshot("settled")
    print(f"  confirmation {rate:.3f}/s for {step['window_s']:.0f} s: {step['overall']}; done: {out}/report.md", flush=True)
    return out


def run_ab(profile_name: str, mix_name: str, rate: float, image_a: str, image_b: str, order: str, out: Path | None) -> Path:
    profile = load_profile(profile_name)
    plan = {**profile["capacity"], "mix": profile["capacity"]["mixes"][mix_name]}
    cluster = Cluster()
    cluster.verify()
    out = out or Path(f"/tmp/bifrost-bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{profile_name}-ab-{mix_name}")
    current = Run(cluster, "ab", profile_name, profile, "fake", out)
    images = {"a": image_a, "b": image_b}
    current.result.update({"mix_name": mix_name, "rate": rate, "order": order, "variants": {
        variant: _image_record(tag, required=True) for variant, tag in images.items()}, "arms": []})
    for index, variant in enumerate(order.lower()):
        print(f"Arm {index + 1}/{len(order)}: {variant} ({images[variant]}) ...", flush=True)
        result = ab.arm(cluster, profile, plan, images[variant], rate,
                        lambda image: _prepare(cluster, current, profile, "fake", image))
        result["image_provenance"] = current.result["api_image_provenance"]
        current.result["arms"].append(result)
        current.save()
        current.result["comparison"] = ab.compare(current.result["arms"], images)
        current.save()
        print(f"  idle {result['idle']['xact_per_s']:.2f} tx/s ({result['idle']['rollbacks_per_s']:.2f} rollbacks/s); "
              f"{result['xact_per_correct']:.1f} tx and {result['api_cpu_s_per_correct'] * 1000:.0f} API CPU ms per correct run; {result['overall']}", flush=True)
    print(f"Done: {out}/report.md", flush=True)
    return out


def run_drill(profile_name: str, mix_name: str, rate: float, kind: str, api_image: str | None, out: Path | None) -> Path:
    if kind == "redis-empty-restart" and rate != 1.0:
        raise SystemExit("D4 Redis restart is correctness-only and must run at 1/s")
    profile = load_profile(profile_name)
    plan = {**profile["capacity"], "mix": profile["capacity"]["mixes"][mix_name]}
    cluster = Cluster()
    cluster.verify()
    out = out or Path(f"/tmp/bifrost-bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{profile_name}-drill-{kind}")
    selected_api_image = api_image or deploy.images(cluster.identity)["api"]
    current = Run(cluster, "drill", profile_name, profile, "fake", out, selected_api_image)
    current.result.update({"mix_name": mix_name, "api_image": selected_api_image})
    print(f"Resetting and seeding '{profile_name}' ...", flush=True)
    deploy.reset(cluster)
    ids = _prepare(cluster, current, profile, "fake", selected_api_image)
    print(f"Drill {kind} at {rate}/s ...", flush=True)
    if kind == "redis-empty-restart":
        current.result.update({"mode": "correctness-only", "not_capacity": True, "rate": 1.0})

        def persist_initial(result: dict) -> None:
            current.result["drill"] = result
            current.save()

        current.result["drill"] = drills.d4(
            cluster, ids, plan, profile["capacity"]["disk"], current.result["run_id"], persist_initial,
        )
        if current.result["drill"].get("requires_namespace_teardown"):
            # The D4 driver may have been removed to stop a framed command.  Do
            # not release this caller until the guarded benchmark namespaces
            # have been removed and that cleanup has been recorded.
            current.save()
            try:
                deploy.reset(cluster)
            except BaseException as error:
                current.result["drill"]["namespace_cleanup"] = {
                    "verified": False, "error": f"{type(error).__name__}: {error}",
                }
                current.save()
                raise
            current.result["drill"]["namespace_cleanup"] = {"verified": True}
            current.save()
    else:
        current.result["drill"] = drills.drill(cluster, ids, plan, kind, rate, profile["capacity"]["disk"])
    current.save()
    result = current.result["drill"]
    if kind == "redis-empty-restart":
        print(f"  correctness-only D4: {result['overall']}; passed {result['passed']}", flush=True)
    else:
        print(f"  failures {result['failures']} of {result['requests']}; first success {result['first_success_after_ready_s']} s after ready; "
              f"checks {result['checks']}; passed {result['passed']}", flush=True)
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
    run_parser.add_argument("--api-image")
    run_parser.add_argument("--out", type=Path)
    capacity_parser = sub.add_parser("capacity")
    capacity_parser.add_argument("--profile", default="m")
    capacity_parser.add_argument("--mix", required=True)
    capacity_parser.add_argument("--api-image")
    capacity_parser.add_argument("--out", type=Path)
    confirm_parser = sub.add_parser("confirm")
    confirm_parser.add_argument("--profile", default="m-capacity")
    confirm_parser.add_argument("--mix", required=True)
    confirm_parser.add_argument("--rate", type=float, required=True)
    confirm_parser.add_argument("--api-image")
    confirm_parser.add_argument("--out", type=Path)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--source", type=Path, required=True)
    build_parser.add_argument("--label", required=True)
    ab_parser = sub.add_parser("ab")
    ab_parser.add_argument("--profile", default="m-capacity")
    ab_parser.add_argument("--mix", required=True)
    ab_parser.add_argument("--rate", type=float, required=True)
    ab_parser.add_argument("--a", required=True, help="API image tag for variant A")
    ab_parser.add_argument("--b", required=True, help="API image tag for variant B")
    ab_parser.add_argument("--order", default="ABBAAB")
    ab_parser.add_argument("--out", type=Path)
    drill_parser = sub.add_parser("drill")
    drill_parser.add_argument("--profile", default="m-capacity")
    drill_parser.add_argument("--mix", default="mixed")
    drill_parser.add_argument("--rate", type=float, required=True)
    drill_parser.add_argument("--kind", choices=["pooler-delete", "pgbouncer-kill", "redis-empty-restart"], required=True)
    drill_parser.add_argument("--api-image")
    drill_parser.add_argument("--out", type=Path)
    sub.add_parser("down")
    args = parser.parse_args()

    if args.command == "up":
        up(load_profile(args.profile))
    elif args.command == "run":
        run_classes(args.profile, None if args.backend == "none" else args.backend, args.out, args.api_image)
    elif args.command == "capacity":
        run_capacity(args.profile, args.mix, args.api_image, args.out)
    elif args.command == "confirm":
        run_confirmation(args.profile, args.mix, args.rate, args.api_image, args.out)
    elif args.command == "build":
        cluster = Cluster()
        cluster.verify()
        print(json.dumps(deploy.build_variant(cluster.identity, args.source.resolve(), args.label)), flush=True)
    elif args.command == "ab":
        run_ab(args.profile, args.mix, args.rate, args.a, args.b, args.order, args.out)
    elif args.command == "drill":
        run_drill(args.profile, args.mix, args.rate, args.kind, args.api_image, args.out)
    elif args.command == "down":
        down()


if __name__ == "__main__":
    main()
