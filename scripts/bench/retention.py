#!/usr/bin/env python3
"""Run a bounded, local-only retention rehearsal with a fresh synthetic fixture.

The rehearsal never changes retention policy, customer data, or an existing
benchmark result.  It creates a clean benchmark tenant, records the complete
terminal records and an immutable receipt, then backdates only those explicit
fixture UUIDs before calling the canonical maintenance PlatformJob twice.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import deploy  # noqa: E402
import guard  # noqa: E402
import run  # noqa: E402
import storage  # noqa: E402
from cluster import Cluster  # noqa: E402


FIXTURE = HERE / "incluster" / "retention_fixture.py"
LOCK_PATH = Path("/tmp/bifrost-capacity-host.lock")


def _write_receipt(out: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Create once, fsync, then hash the exact raw terminal evidence before mutation."""
    path = out / "fixture-terminal-receipt.json"
    contents = json.dumps({"terminal_records": records}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": len(contents),
            "created_at": _utc_now()}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_fresh(path: Path) -> None:
    if path.exists():
        raise ValueError(f"retention output must be fresh: {path}")


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _fixture(cluster: Cluster, pod: str, mode: str, arguments: dict[str, Any], *, timeout_s: int = 1800) -> dict[str, Any]:
    return _object(cluster.exec_python(pod, FIXTURE, [mode, json.dumps(arguments)], timeout_s=timeout_s,
                                       namespace=guard.TENANT_NS if pod != "bench-driver" else guard.INFRA_NS), mode)


def _api_pod(cluster: Cluster) -> str:
    pod, _uid = cluster.api_pod()
    if not isinstance(pod, str) or not pod:
        raise RuntimeError("benchmark API pod identity is missing")
    return pod


def _expected_ids(records: dict[str, Any]) -> list[str]:
    terminal = records.get("terminal_records")
    if not isinstance(terminal, list) or not terminal:
        raise RuntimeError("fixture did not retain terminal records")
    ids = [item.get("execution_id") for item in terminal if isinstance(item, dict)]
    if len(ids) != len(terminal) or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise RuntimeError("fixture terminal records do not contain unique execution IDs")
    return sorted(ids)


def _retention_days(settings: dict[str, Any]) -> int | None:
    configured = _object(settings.get("settings"), "run-retention settings.settings")
    days = configured.get("days")
    if days is None:
        return None
    if type(days) is not int or days < 30:
        raise RuntimeError("canonical run-retention settings returned invalid days")
    return days


def _assert_state(observed: dict[str, Any], ids: list[str], *, deleted: bool) -> None:
    found = observed.get("execution_ids")
    if not isinstance(found, list):
        raise RuntimeError("fixture inspection omitted execution IDs")
    if deleted:
        if found or observed.get("execution_count") != 0 or observed.get("execution_log_count") != 0:
            raise RuntimeError("retention did not delete exactly the fixture executions and cascaded logs")
    elif sorted(found) != ids or observed.get("execution_count") != len(ids):
        raise RuntimeError("fixture inspection does not match the declared execution IDs")


@contextmanager
def _host_lock() -> Iterator[None]:
    """Serialize any future local rehearsal with benchmark capacity work."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def rehearse(profile_name: str, api_image: str, out: Path) -> Path:
    """Perform one destructive-to-fixture-only rehearsal and write fresh evidence."""
    _require_fresh(out)
    profile = run.load_profile(profile_name)
    cluster = Cluster()
    cluster.verify()
    current = run.Run(cluster, "retention-rehearsal", profile_name, profile, "fake", out, api_image)
    current.result.update({
        "api_image": api_image,
        "method": {
            "fixture": "ordinary authenticated workflow executions; no custom table or parallel insertion path",
            "backdate": "API-pod SQL constrained to explicit fresh terminal fixture UUIDs after immutable receipt",
            "retention": "canonical POST /api/maintenance/run-retention/run dry-run then actual PlatformJob",
            "physical_limit": "DELETE and autovacuum need not shrink allocated PostgreSQL PVC bytes",
        },
        "pricing": {"status": "not_measured", "reason": "backup/restore and provider pricing are outside this local rehearsal"},
        "audit_artifact_retention": {
            "status": "settings_only",
            "reason": "canonical policies are recorded in each storage frame; audit/artifact cleanup is not exercised",
        },
    })
    failure: BaseException | None = None
    try:
        current.result["stage"] = "resetting"
        current.save()
        deploy.reset(cluster)
        ids = run._prepare(cluster, current, profile, "fake", api_image)
        run.check_destinations(cluster, ids)
        current.result["setup"] = {"base": ids}
        current.result["stage"] = "fixture_prepare"
        current.save()
        fixture = _fixture(cluster, "bench-driver", "prepare", {})
        current.result["fixture"] = fixture
        current.result["source_hashes"] = {"retention_fixture.py": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                                           "fixture_workflow": fixture.get("fixture", {}).get("source_sha256")}
        current.result["before"] = {"storage": storage.collect(cluster)}
        canonical_settings = _fixture(cluster, "bench-driver", "settings", {})
        retention_days = _retention_days(canonical_settings)
        current.result["canonical_run_retention_settings"] = canonical_settings
        if retention_days is None:
            current.result.update({
                "stage": "not_exercised",
                "not_exercised": "canonical run retention is keep_forever; no policy mutation or simulated deletion was performed",
                "after": {"storage": storage.collect(cluster)},
            })
            return out
        current.result["stage"] = "fixture_execute"
        current.save()
        workflow_ids = _object(fixture.get("workflow_ids"), "fixture.workflow_ids")
        phase_records: list[dict[str, Any]] = []
        current.result["fixture_phases"] = []
        for case in ("small_success", "small_failed", "repetitive_5mb", "entropy_5mb"):
            records = _fixture(cluster, "bench-driver", "execute", {"workflow_ids": workflow_ids, "case": case}, timeout_s=3600)
            phase_records.extend(records.get("terminal_records", []))
            phase = {"case": case, "records": records, "storage_after": storage.collect(cluster)}
            current.result["fixture_phases"].append(phase)
            current.save()
        records = {"terminal_records": phase_records}
        execution_ids = _expected_ids(records)
        case_by_id = {item["execution_id"]: item["case"] for item in phase_records if isinstance(item, dict)}
        if len(case_by_id) != len(execution_ids):
            raise RuntimeError("fixture terminal records do not retain one case per execution ID")
        receipt = _write_receipt(out, phase_records)
        current.result["fixture_records"] = records
        current.result["immutable_receipt"] = {**receipt, "execution_ids": execution_ids}
        current.save()
        api_pod = _api_pod(cluster)
        before_backdate = _fixture(cluster, api_pod, "inspect", {"ids": execution_ids, "case_by_id": case_by_id})
        _assert_state(before_backdate, execution_ids, deleted=False)
        current.result["before_backdate"] = before_backdate
        current.result["backdate"] = _fixture(cluster, api_pod, "backdate", {
            "ids": execution_ids, "receipt_sha256": receipt["sha256"], "age_days": retention_days + 1,
        })
        current.result["before_retention"] = {"storage": storage.collect(cluster), "fixture": _fixture(
            cluster, api_pod, "inspect", {"ids": execution_ids, "case_by_id": case_by_id})}
        current.result["stage"] = "retention_dry_run"
        current.save()
        current.result["retention_dry_run"] = _fixture(cluster, "bench-driver", "maintenance", {"dry_run": True})
        current.result["stage"] = "retention_actual"
        current.save()
        current.result["retention_actual"] = _fixture(cluster, "bench-driver", "maintenance", {"dry_run": False})
        after_fixture = _fixture(cluster, api_pod, "inspect", {"ids": execution_ids, "case_by_id": case_by_id})
        _assert_state(after_fixture, execution_ids, deleted=True)
        current.result["after"] = {"storage": storage.collect(cluster), "fixture": after_fixture}
        current.result["stage"] = "complete"
    except BaseException as error:
        failure = error
        current.result.update({"stage": "exception", "error": f"{type(error).__name__}: {error}"})
        current.save()
    finally:
        try:
            deploy.reset(cluster)
            current.result["cleanup"] = {"verified": True}
        except BaseException as cleanup_error:
            current.result["cleanup"] = {"verified": False, "error": f"{type(cleanup_error).__name__}: {cleanup_error}"}
            if failure is None:
                failure = cleanup_error
        current.result["finished_at"] = _utc_now()
        current.save()
    if failure is not None:
        raise failure
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="m")
    parser.add_argument("--api-image", required=True, help="Explicit local candidate API image tag")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get("DOCKER_HOST", "").startswith(("tcp://", "ssh://")):
        raise SystemExit("retention rehearsal requires a local Docker socket")
    with _host_lock():
        print(rehearse(args.profile, args.api_image, args.out))


if __name__ == "__main__":
    main()
