#!/usr/bin/env python3
"""Standalone authenticated V2 browser benchmark for the public interactive fixture.

The CLI owns the same host lock as capacity work.  It resets only the guarded
Kind namespaces, invokes the established interactive workflow fixture harness,
then deploys one independent V2 App through the normal source-zip API before
opening browser pages from a prepared in-cluster runner image.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import browser  # noqa: E402
import browser_preparation  # noqa: E402
import deploy  # noqa: E402
import guard  # noqa: E402
import integration_diagnostic  # noqa: E402
import run as bench_run  # noqa: E402
from cluster import Cluster, image_id_digest  # noqa: E402

APP_DRIVER = HERE / "incluster" / "interactive_app.py"
LOCK_PATH = Path("/tmp/bifrost-capacity-host.lock")
BROWSER_POD = "bench-browser-runner"
BROWSER_CONFIG = "bench-browser-runner-config"
_DIGEST_REF = re.compile(r"^[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}$")
_LOCAL_IMAGE_REF = re.compile(r"^[a-z0-9][a-z0-9./:_-]*:[a-z0-9][a-z0-9._-]*$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


@contextmanager
def host_lock() -> Iterator[None]:
    """Acquire the canonical benchmark lock exactly once, at the CLI boundary."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another benchmark holds the capacity host lock") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _exact_digest(reference: str, label: str) -> str:
    if not _DIGEST_REF.fullmatch(reference):
        raise ValueError(f"{label} must be an exact image reference with a 64-hex sha256 digest")
    return reference


def _prepared_image(reference: str, image_id: str) -> tuple[str, str]:
    """A loaded local tag paired with the immutable config digest it resolves to.

    Kind's ``load docker-image`` preserves a local tag, not a registry manifest
    ref, so putting ``repo@config-digest`` in a Pod would be an invalid promise.
    Keep the selected tag and its Docker/CRI config digest as one receipt.
    """
    if not _LOCAL_IMAGE_REF.fullmatch(reference):
        raise ValueError("prepared browser image must be an explicit local name:tag")
    if not _IMAGE_ID.fullmatch(image_id):
        raise ValueError("prepared browser image ID must be a 64-hex sha256 digest")
    return reference, image_id


def load_cache_receipt(path: Path, browser_image: str, browser_image_id: str) -> dict:
    """Refuse a run unless the prepared runner was loaded before measurement."""
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read browser cache receipt: {error}") from error
    if (not isinstance(receipt, dict) or receipt.get("image") != browser_image
            or receipt.get("image_id") != browser_image_id
            or receipt.get("cached_before_measurement") is not True):
        raise ValueError("browser cache receipt does not prove the selected image was loaded before measurement")
    return {
        "image": receipt["image"], "image_id": receipt["image_id"],
        "cached_before_measurement": True, "loaded_at": receipt.get("loaded_at"),
    }


def load_build_cache_receipt(path: Path, cached_build_image: str) -> dict:
    """Require the exact fixture npm cache image before the normal deploy."""
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read build cache receipt: {error}") from error
    if not _LOCAL_IMAGE_REF.fullmatch(cached_build_image):
        raise ValueError("cached build image must be an explicit local name:tag")
    if (
        not isinstance(receipt, dict)
        or receipt.get("image") != cached_build_image
        or not isinstance(receipt.get("image_id"), str)
        or not _IMAGE_ID.fullmatch(receipt["image_id"])
        or receipt.get("cached_before_measurement") is not True
    ):
        raise ValueError("build cache receipt does not prove the selected image was loaded before measurement")
    if receipt.get("fixture_lock_sha256") != browser.fixture_lock_sha256():
        raise ValueError("build cache receipt does not match the fixture lock")
    return {
        "image": receipt["image"],
        "image_id": receipt["image_id"],
        "cached_before_measurement": True,
        "fixture_lock_sha256": receipt["fixture_lock_sha256"],
        "loaded_at": receipt.get("loaded_at"),
    }


def browser_pod(image: str, config_name: str = BROWSER_CONFIG) -> dict:
    """A finite 1 CPU/1 GiB runner outside the tenant's resource envelope."""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": BROWSER_POD,
            "namespace": guard.INFRA_NS,
            "labels": {"app.kubernetes.io/name": BROWSER_POD, "bifrost.dev/bench": "browser"},
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "containers": [{
                "name": "browser",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["sh", "-c", "node /bench/driver.mjs \"$BENCH_CONFIG\""],
                "env": [{
                    "name": "BENCH_CONFIG",
                    "valueFrom": {"configMapKeyRef": {"name": config_name, "key": "config"}},
                }],
                "envFrom": [{"secretRef": {"name": "bench-infra"}}],
                "resources": {
                    "requests": {"cpu": "1", "memory": "1Gi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                },
            }],
        },
    }


def browser_config(app_slug: str) -> dict:
    """Configuration consumed by the prepared runner, after TLS preparation."""
    if not isinstance(app_slug, str) or not app_slug:
        raise ValueError("deployed browser app needs a nonempty slug")
    return {
        "client_url": browser_preparation.TLS_ORIGIN,
        "app_slug": app_slug,
        "batches": browser.observation_batches(),
    }


def _apply_browser_config(cluster: Cluster, config: dict) -> None:
    rendered = cluster.kubectl(
        "create", "configmap", BROWSER_CONFIG,
        f"--from-literal=config={json.dumps(config, separators=(',', ':'))}",
        "--dry-run=client", "-o", "json", "-n", guard.INFRA_NS, namespaced=False,
    )
    cluster.kubectl("apply", "-f", "-", input=rendered, namespaced=False)


def _pod_terminal(cluster: Cluster, timeout_s: int = 1800) -> tuple[dict, str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pod = json.loads(cluster.kubectl("get", "pod", BROWSER_POD, "-o", "json", namespace=guard.INFRA_NS))
        phase = pod.get("status", {}).get("phase")
        if phase in {"Succeeded", "Failed"}:
            logs = cluster.kubectl("logs", BROWSER_POD, "-c", "browser", namespace=guard.INFRA_NS, check=False)
            return pod, logs
        time.sleep(1)
    raise TimeoutError(f"browser runner pod did not finish within {timeout_s}s")


def _browser_diagnostics(cluster: Cluster) -> dict[str, object]:
    """Read whatever browser evidence remains without making cleanup depend on it."""
    captured: dict[str, object] = {}
    try:
        raw_pod = cluster.kubectl(
            "get", "pod", BROWSER_POD, "-o", "json", namespace=guard.INFRA_NS, check=False
        )
        pod = json.loads(raw_pod)
        if isinstance(pod, dict):
            captured["pod"] = pod
    except (json.JSONDecodeError, TypeError, ValueError, RuntimeError):
        pass
    try:
        logs = cluster.kubectl(
            "logs", BROWSER_POD, "-c", "browser", namespace=guard.INFRA_NS, check=False
        )
        if isinstance(logs, str):
            captured["logs"] = logs
    except RuntimeError:
        pass
    return captured


def _retain_browser_raw(current: bench_run.Run, evidence: dict[str, object]) -> None:
    """Persist raw driver evidence before a later validation can reject it."""
    browser_result = current.result["interactive"].setdefault("browser", {})
    raw = browser_result.setdefault("raw", {})
    raw.update(evidence)
    current.save()


def _runner_image_id(pod: dict) -> str:
    statuses = [item for item in pod.get("status", {}).get("containerStatuses", []) if item.get("name") == "browser"]
    if len(statuses) != 1 or not isinstance(statuses[0].get("imageID"), str):
        raise RuntimeError("browser runner pod has no immutable browser container image ID")
    return image_id_digest(statuses[0]["imageID"])


def _cleanup_browser(cluster: Cluster) -> None:
    cluster.kubectl("delete", "pod", BROWSER_POD, "-n", guard.INFRA_NS, "--ignore-not-found", "--wait=true", namespaced=False, check=False)
    cluster.kubectl("delete", "configmap", BROWSER_CONFIG, "-n", guard.INFRA_NS, "--ignore-not-found", "--wait=true", namespaced=False, check=False)


def _terminal_cleanup(cluster: Cluster, current: bench_run.Run, original_error: BaseException | None) -> None:
    """Remove every benchmark namespace after either terminal outcome.

    Cleanup failure is durable evidence.  It becomes the outcome only when the
    benchmark body succeeded, so it cannot hide the failure that triggered
    cleanup in the first place.
    """
    receipt: dict[str, object] = {"started_at": _now(), "browser_resources": None, "namespaces": None, "errors": []}
    errors: list[BaseException] = []
    try:
        cluster.verify()
        _cleanup_browser(cluster)
        receipt["browser_resources"] = {"verified": True}
    except BaseException as error:
        errors.append(error)
        receipt["browser_resources"] = {"verified": False, "error": f"{type(error).__name__}: {error}"}
    try:
        cluster.verify()
        deploy.reset(cluster)
        cluster.verify()
        receipt["namespaces"] = {"verified": True}
    except BaseException as error:
        errors.append(error)
        receipt["namespaces"] = {"verified": False, "error": f"{type(error).__name__}: {error}"}
    receipt["finished_at"] = _now()
    current.result["interactive"]["cleanup"] = receipt
    receipt["errors"] = [f"{type(item).__name__}: {item}" for item in errors]
    try:
        current.save()
    except BaseException as error:
        errors.append(error)
        receipt["errors"] = [f"{type(item).__name__}: {item}" for item in errors]
        try:
            current.save()
        except BaseException:
            pass
    if errors and original_error is None:
        raise errors[0]


def _durable_browser_records(cluster: Cluster, validated: list[dict]) -> list[dict]:
    requested = [item["execution_id"] for item in validated]
    durable = cluster.exec_python("bench-driver", APP_DRIVER, ["records", json.dumps({"execution_ids": requested})], timeout_s=300)["records"]
    by_id = {row["id"]: row for row in durable}
    if set(by_id) != set(requested):
        raise RuntimeError("durable browser records do not match observed execution IDs")
    for item in validated:
        row = by_id[item["execution_id"]]
        result = row.get("result")
        if not isinstance(result, dict) or result.get("request_id") != item["request_id"]:
            raise RuntimeError("durable browser result does not match its generated request ID")
        if result.get("variant") != item["variant"]:
            raise RuntimeError("durable browser result variant differs from its page variant")
    for variant in browser.VARIANTS:
        variant_records = [
            by_id[item["execution_id"]]
            for item in validated
            if item["variant"] == variant
        ]
        if not variant_records:
            continue
        evidence = integration_diagnostic.validate_execution_records(variant_records, variant)
        if evidence["overall"] != "pass":
            raise RuntimeError(f"durable browser records fail the {variant} fixture contract: {evidence['failures']}")
    return durable


def _worker_policy(cluster: Cluster, action: str, values: dict | None = None) -> dict:
    """Use the authenticated benchmark helper for the existing policy API."""
    result = cluster.exec_python(
        "bench-driver", APP_DRIVER, ["policy", json.dumps({"action": action, "values": values})], timeout_s=300
    )
    policy = result.get("policy")
    if not isinstance(policy, dict):
        raise RuntimeError("worker policy helper did not return a policy")
    return policy


def _restore_build_preparation(
    cluster: Cluster,
    runtime_previous: dict[str, str | None] | None,
    policy_original: dict | None,
) -> dict[str, object]:
    """Restore the shared build identity and worker policy before browser timing."""
    receipt: dict[str, object] = {"runtime": None, "policy": None}
    errors: list[BaseException] = []
    if runtime_previous is not None:
        try:
            browser_preparation.restore_build_runtime(cluster, runtime_previous)
            receipt["runtime"] = {"restored": True}
        except BaseException as error:
            errors.append(error)
            receipt["runtime"] = {"restored": False, "error": f"{type(error).__name__}: {error}"}
    if policy_original is not None:
        try:
            restored = browser_preparation.restore_policy(policy_original)
            _worker_policy(cluster, "update", restored)
            workers = browser_preparation.wait_for_worker_count(cluster, restored["warm_workers"])
            receipt["policy"] = {"restored": True, "workers": workers}
        except BaseException as error:
            errors.append(error)
            receipt["policy"] = {"restored": False, "error": f"{type(error).__name__}: {error}"}
    if errors:
        raise errors[0]
    return receipt


def run(profile_name: str, api_image: str, browser_image: str, browser_image_id: str, browser_base_image: str,
        cache_receipt: Path, cached_build_image: str, build_cache_receipt: Path, out: Path) -> Path:
    """Prepare normal fixtures once, then measure the real deployed-app browser path.

    Callers must hold :func:`host_lock`; this lets tests exercise the
    orchestration without taking a global host lock.
    """
    profile = bench_run.load_profile(profile_name)
    if not profile.get("interactive_integration_diagnostic"):
        raise ValueError("interactive browser benchmark requires an interactive_integration_diagnostic profile")
    browser_image, browser_image_id = _prepared_image(browser_image, browser_image_id)
    browser_base_image = _exact_digest(browser_base_image, "Playwright base image")
    if not browser_base_image.startswith(f"{browser.BROWSER_RUNNER_IMAGE}@"):
        raise ValueError("Playwright base image must use the benchmark's pinned Playwright version")
    cache = load_cache_receipt(cache_receipt, browser_image, browser_image_id)
    build_cache = load_build_cache_receipt(build_cache_receipt, cached_build_image)
    if out.exists():
        raise ValueError(f"interactive output already exists: {out}")

    cluster = Cluster()
    cluster.verify()
    build_image_provenance = cluster.image_provenance(cached_build_image, build_cache["image_id"])
    current = bench_run.Run(cluster, "interactive", profile_name, profile, None, out, api_image)
    current.result.update({
        "interactive": {
            "started_at": _now(),
            "browser_image": browser_image,
            "browser_image_id": browser_image_id,
            "playwright_base_image": browser_base_image,
            "cache_receipt": cache,
            "build_cache_receipt": build_cache,
            "build_cache_image_provenance": build_image_provenance,
            "build_preparation": None,
            "browser": None,
        },
    })
    current.save()
    browser_started = False
    try:
        # The browser path only needs the normal fixture/editor/SDK setup.  It
        # deliberately does not run the backend workload classes a second time.
        cluster.verify()
        deploy.reset(cluster)
        bench_run._prepare(cluster, current, profile, None, api_image)

        tls = browser_preparation.prepare_tls(cluster)
        policy_original = _worker_policy(cluster, "read")
        policy_prepare = browser_preparation.build_policy_prepare(policy_original)
        runtime_previous: dict[str, str | None] | None = None
        current.result["interactive"]["build_preparation"] = {
            "tls": tls,
            "policy_prepare": policy_prepare,
            "runtime": None,
            "restoration": None,
        }
        current.save()
        try:
            _worker_policy(cluster, "update", policy_prepare)
            drained = browser_preparation.wait_for_worker_count(cluster, 0)
            runtime_previous, runtime = browser_preparation.prepare_build_runtime(cluster, cached_build_image)
            current.result["interactive"]["build_preparation"]["runtime"] = {
                **runtime, "drained_workers": drained,
            }
            current.save()
            source = browser.write_source_zip(out / "browser-fixture.zip", browser.source_files())
            app = cluster.exec_python("bench-driver", APP_DRIVER, ["deploy", json.dumps({
                "source_b64": base64.b64encode((out / "browser-fixture.zip").read_bytes()).decode("ascii"),
                "name": "Bench Browser Interactive Fixture",
                "slug": f"bench-browser-{uuid4().hex[:12]}",
            })], timeout_s=1200)
        finally:
            restoration = _restore_build_preparation(cluster, runtime_previous, policy_original)
            current.result["interactive"]["build_preparation"]["restoration"] = restoration
            current.save()
        current.result["interactive"]["build"] = {
            "source": source,
            "accepted": app.get("accepted"),
            "job": app.get("job"),
        }
        application_read_error = app.get("application_read_error")
        if application_read_error is not None:
            current.result["interactive"]["build"]["application_read_error"] = application_read_error
            current.save()
            raise RuntimeError(f"could not read deployed application metadata: {application_read_error}")
        application = app.get("application")
        if not isinstance(application, dict):
            current.save()
            raise RuntimeError("application.deploy helper returned no deployed application metadata")
        build = browser.build_receipt(source, app["accepted"], app["job"], application)
        current.result["interactive"]["build"] = build
        current.save()

        config = browser_config(application["slug"])
        current.result["interactive"]["browser"] = {"config": config, "raw": {}}
        current.save()
        browser_started = True
        cluster.verify()
        _apply_browser_config(cluster, config)
        cluster.kubectl("apply", "-f", "-", input=json.dumps(browser_pod(browser_image)), namespaced=False)
        before = cluster.container_stats(namespace=guard.INFRA_NS)
        pod, logs = _pod_terminal(cluster)
        _retain_browser_raw(current, {"pod": pod, "logs": logs})
        after = cluster.container_stats(namespace=guard.INFRA_NS)
        runner = browser.runner_receipt(browser_base_image, _runner_image_id(pod), True)
        if runner["image_id"] != browser_image_id:
            raise RuntimeError("browser runner pod image ID differs from the preloaded runner receipt")
        runner["runner_image"] = browser_image
        runner["pod"] = {"name": BROWSER_POD, "uid": pod.get("metadata", {}).get("uid")}
        runs = browser.decode_driver_output(logs)
        _retain_browser_raw(current, {"runs": runs})
        browser.validate_run_matrix(runs)
        validated = [{**browser.validate_events(run["events"]), "variant": run["variant"], "ordinal": run["ordinal"], "stage": run["stage"], "outcome": run["outcome"]} for run in runs]
        durable = _durable_browser_records(cluster, validated)
        current.result["interactive"]["browser"].update({
            "runner": runner,
            "cgroup": {"before": before, "after": after},
            "observed": validated,
            "durable_records": durable,
        })
        if pod.get("status", {}).get("phase") != "Succeeded" or any(item["outcome"] != "success" for item in validated):
            raise RuntimeError("browser runner completed with a terminal workflow failure")
        current.result["interactive"]["completed_at"] = _now()
        current.save()
        return out
    except BaseException as error:
        if browser_started:
            try:
                _retain_browser_raw(current, _browser_diagnostics(cluster))
            except BaseException:
                pass
        current.result["interactive"]["error"] = f"{type(error).__name__}: {error}"
        current.save()
        raise
    finally:
        _terminal_cleanup(cluster, current, sys.exc_info()[1])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="m-interactive-integration")
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--browser-image", required=True, help="prepared local browser runner image name:tag")
    parser.add_argument("--browser-image-id", required=True, help="prepared browser runner's exact sha256 config digest")
    parser.add_argument("--browser-base-image", required=True, help="pinned mcr Playwright base image digest used to build the runner")
    parser.add_argument("--browser-cache-receipt", type=Path, required=True)
    parser.add_argument("--cached-build-image", required=True, help="prepared local API image with the fixture npm cache")
    parser.add_argument("--build-cache-receipt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with host_lock():
        print(run(args.profile, args.api_image, args.browser_image, args.browser_image_id, args.browser_base_image, args.browser_cache_receipt, args.cached_build_image, args.build_cache_receipt, args.out), flush=True)


if __name__ == "__main__":
    main()
