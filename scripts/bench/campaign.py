"""Immutable five-window mixed-confirmation campaign orchestration."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import capacity
import deploy
import jsoncodec
import preflight
import search
from cluster import Cluster

PLAN = "mixed-confirmation-go-5-v1"
MIX_NAME = "mixed"
RATE_PER_S = 2.236
SEGMENT_COUNT = 5
WARMUP_S = 90
MEASURED_WINDOW_S = 3600
DRAIN_S = 120
RELIABILITY_LIMIT = 0.001
RELIABILITY_OBSERVATIONS = 2995
SEGMENT_STATUSES = frozenset({"pass", "fail", "invalid", "partial", "inconclusive"})
GATE_STATUSES = frozenset({"pass", "fail", "inconclusive"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_value(value: object) -> str:
    return _sha256_bytes(_json_bytes(value))


def _atomic_create(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _atomic_replace(path: Path, contents: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        _atomic_create(temporary, contents)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _json_object(contents: bytes, path: Path) -> dict:
    value = json.loads(contents)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _hash_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".sha256")


def _write_hash_create(path: Path, contents: bytes) -> None:
    _atomic_create(_hash_path(path), f"{_sha256_bytes(contents)}\n".encode("ascii"))


def _verify_hash(path: Path) -> bytes:
    contents = path.read_bytes()
    expected = _hash_path(path).read_text(encoding="ascii").strip()
    actual = _sha256_bytes(contents)
    if expected != actual:
        raise ValueError(f"hash mismatch for {path}")
    return contents


def _manifest_path(out: Path) -> Path:
    return out / "campaign-manifest.json"


def _segment_path(out: Path, index: int) -> Path:
    return out / "segments" / f"segment-{index}.json"


def _preflight_path(manifest_path: Path) -> Path:
    return manifest_path.parent / "campaign-preflight.json"


def _resolved_plan(profile: dict) -> dict:
    plan = profile["capacity"]
    if plan["warmup_s"] != WARMUP_S or plan["drain_s"] != DRAIN_S:
        raise ValueError("campaign requires the approved 90-second warm-up and 120-second drain")
    if MIX_NAME not in plan["mixes"]:
        raise ValueError(f"campaign profile does not define mix {MIX_NAME!r}")
    return {**plan, "mix": plan["mixes"][MIX_NAME]}


def _eligible_classes(mix: dict[str, float]) -> list[str]:
    total = sum(mix.values())
    return sorted(name for name, weight in mix.items() if weight / total >= 0.10)


def _frozen_git_sha() -> str:
    import run

    sha = run._git_sha()
    if not isinstance(sha, str) or not sha or sha.endswith("-dirty"):
        raise ValueError("campaign requires a clean, nonempty frozen harness revision")
    return sha


def create_manifest(cluster: Cluster, profile_name: str, api_image: str, out: Path) -> Path:
    """Freeze every campaign input before any tenant reset or deployment."""
    import run

    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"campaign output already contains evidence: {out}")
    harness_git_sha = _frozen_git_sha()
    cluster.verify()
    profile = run.load_profile(profile_name)
    plan = _resolved_plan(profile)
    image = run._image_record(api_image, required=True)
    provenance = cluster.image_provenance(api_image, image["id"])
    seeds = [search.confirmation_seed(plan, index) for index in range(SEGMENT_COUNT)]
    manifest_path = _manifest_path(out)
    segments = [{
        "index": index,
        "seed": seed,
        "output_path": str(_segment_path(out.resolve(), index)),
        "hash_path": str(_hash_path(_segment_path(out.resolve(), index))),
    } for index, seed in enumerate(seeds)]
    manifest = {
        "plan": PLAN,
        "created_at": _utc_now(),
        "profile_name": profile_name,
        "resolved_profile_sha256": _sha256_value(profile),
        "harness_git_sha": harness_git_sha,
        "api_image": {**image, "provenance": provenance},
        "mix_name": MIX_NAME,
        "mix": plan["mix"],
        "rate_per_s": RATE_PER_S,
        "warmup_s": WARMUP_S,
        "measured_window_s": MEASURED_WINDOW_S,
        "drain_s": DRAIN_S,
        "thresholds_source": plan["thresholds_source"],
        "thresholds": plan["thresholds"],
        "deadlines_s": plan["deadlines_s"],
        "eligible_classes": _eligible_classes(plan["mix"]),
        "indices": list(range(SEGMENT_COUNT)),
        "seeds": seeds,
        "segments": segments,
    }
    contents = _json_bytes(manifest)
    _atomic_create(manifest_path, contents)
    _write_hash_create(manifest_path, contents)
    return manifest_path


def load_manifest(path: Path) -> tuple[dict, str]:
    """Read the immutable plan and prove the manifest has not changed."""
    contents = _verify_hash(path)
    manifest = _json_object(contents, path)
    if manifest.get("plan") != PLAN:
        raise ValueError(f"unsupported campaign plan {manifest.get('plan')!r}")
    segments = manifest.get("segments")
    if (
        manifest.get("indices") != list(range(SEGMENT_COUNT))
        or type(segments) is not list
        or len(segments) != SEGMENT_COUNT
    ):
        raise ValueError("campaign manifest must declare exactly the approved five indices")
    if manifest.get("rate_per_s") != RATE_PER_S or manifest.get("measured_window_s") != MEASURED_WINDOW_S:
        raise ValueError("campaign manifest does not have the approved fixed rate/window")
    if not all(type(segment) is dict for segment in segments):
        raise ValueError("campaign manifest segments must be objects")
    if manifest.get("seeds") != [segment.get("seed") for segment in segments]:
        raise ValueError("campaign manifest seed list does not match its declared segments")
    if [segment.get("index") for segment in segments] != list(range(SEGMENT_COUNT)):
        raise ValueError("campaign manifest segments must have the approved ordered indices")
    if not all(
        type(segment.get("seed")) is int
        and type(segment.get("output_path")) is str
        and segment.get("hash_path") == str(_hash_path(Path(segment["output_path"])))
        for segment in segments
    ):
        raise ValueError("campaign manifest segments must declare integer seeds and output paths")
    if len(set(manifest["seeds"])) != SEGMENT_COUNT:
        raise ValueError("campaign manifest seeds must be distinct")
    _validate_manifest_evidence(manifest)
    return manifest, _sha256_bytes(contents)


def _validate_manifest_evidence(manifest: dict) -> None:
    mix = manifest.get("mix")
    if type(mix) is not dict or not mix:
        raise ValueError("campaign manifest mix must be a nonempty object")
    if any(
        type(name) is not str
        or type(weight) not in (int, float)
        or not math.isfinite(weight)
        or weight <= 0
        for name, weight in mix.items()
    ):
        raise ValueError("campaign manifest mix weights must be positive finite numbers")
    if manifest.get("mix_name") != MIX_NAME or manifest.get("eligible_classes") != _eligible_classes(mix):
        raise ValueError("campaign manifest eligible classes do not match its frozen mix")
    if manifest.get("warmup_s") != WARMUP_S or manifest.get("drain_s") != DRAIN_S:
        raise ValueError("campaign manifest does not have the approved warm-up/drain")
    deadlines = manifest.get("deadlines_s")
    if (
        type(deadlines) is not dict
        or set(deadlines) != set(mix)
        or any(not _finite_number(value) or value <= 0 for value in deadlines.values())
    ):
        raise ValueError("campaign manifest deadlines must cover every mix class with positive finite values")


def _verify_frozen_inputs(cluster: Cluster, manifest: dict) -> tuple[dict, dict]:
    import run

    profile = run.load_profile(manifest["profile_name"])
    if _sha256_value(profile) != manifest["resolved_profile_sha256"]:
        raise ValueError("resolved profile no longer matches campaign manifest")
    plan = _resolved_plan(profile)
    frozen = {
        "mix": manifest["mix"], "rate_per_s": manifest["rate_per_s"],
        "thresholds_source": manifest["thresholds_source"],
        "thresholds": manifest["thresholds"],
        "deadlines_s": manifest["deadlines_s"],
    }
    current = {"mix": plan["mix"], "rate_per_s": RATE_PER_S,
               "thresholds_source": plan["thresholds_source"],
               "thresholds": plan["thresholds"], "deadlines_s": plan["deadlines_s"]}
    if _json_bytes(frozen) != _json_bytes(current):
        raise ValueError("capacity mix or thresholds no longer match campaign manifest")
    expected_seeds = [search.confirmation_seed(plan, index) for index in range(SEGMENT_COUNT)]
    if manifest["seeds"] != expected_seeds:
        raise ValueError("campaign manifest seeds do not match the frozen confirmation seed rule")
    if _frozen_git_sha() != manifest["harness_git_sha"]:
        raise ValueError("harness revision no longer matches campaign manifest")
    image = manifest["api_image"]
    actual = run._image_record(image["tag"], required=True)
    if actual != {"tag": image["tag"], "id": image["id"]}:
        raise ValueError("selected API image no longer matches campaign manifest")
    provenance = cluster.image_provenance(image["tag"], image["id"])
    if _json_bytes(provenance) != _json_bytes(image["provenance"]):
        raise ValueError("selected API image provenance no longer matches campaign manifest")
    return profile, plan


class _SegmentRun:
    """Minimal ``run._prepare`` adapter that saves only the segment evidence."""

    def __init__(self, cluster: Cluster, out: Path, result: dict) -> None:
        self.cluster, self.out, self.result = cluster, out, result
        self.expected_api_uid: str | None = None

    def save(self) -> None:
        _atomic_replace(self.out, json.dumps(jsoncodec.encode(self.result), indent=2, allow_nan=False).encode("utf-8"))

    def driver(self, mode: str, args: dict, timeout_s: int) -> dict:
        import run

        return self.cluster.exec_python("bench-driver", run.DRIVER, [mode, json.dumps(args)], timeout_s=timeout_s)

    def snapshot(self, label: str) -> None:
        import run

        run.snapshot(self, label)


def _segment_status(step: dict) -> str:
    status = step.get("overall")
    if status in SEGMENT_STATUSES:
        return status
    return "invalid"


def run_segment(manifest_path: Path, index: int) -> Path:
    """Run exactly one declared, previously unstarted campaign segment."""
    import run

    manifest, manifest_hash = load_manifest(manifest_path)
    declared = next((segment for segment in manifest["segments"] if segment["index"] == index), None)
    if declared is None:
        raise ValueError(f"segment index {index} is not declared by this campaign")
    output = Path(declared["output_path"])
    if output.exists() or _hash_path(output).exists():
        raise ValueError(f"campaign segment {index} is already started and cannot be replaced")
    _require_passing_preflight(manifest_path, manifest_hash)

    cluster = Cluster()
    cluster.verify()
    profile, plan = _verify_frozen_inputs(cluster, manifest)
    result = {
        "kind": "mixed-confirmation-segment", "status": "partial", "stage": "declared",
        "campaign_manifest_path": str(manifest_path.resolve()), "campaign_manifest_sha256": manifest_hash,
        "index": index, "seed": declared["seed"], "started_at": _utc_now(),
        "profile_name": manifest["profile_name"], "resolved_profile_sha256": manifest["resolved_profile_sha256"],
        "api_image": manifest["api_image"], "mix": manifest["mix"], "rate_per_s": RATE_PER_S,
        "warmup_s": WARMUP_S, "measured_window_s": MEASURED_WINDOW_S, "drain_s": DRAIN_S,
        "api_pod_changes": 0,
        "snapshots": [],
    }
    current = _SegmentRun(cluster, output, result)
    _atomic_create(output, json.dumps(jsoncodec.encode(result), indent=2, allow_nan=False).encode("utf-8"))
    failure: BaseException | None = None

    def observe(event: dict) -> None:
        result.setdefault("attempt", {}).setdefault("events", []).append(event)

    def checkpoint(evidence: dict) -> None:
        attempt = result.setdefault("attempt", {})
        phase = evidence["phase"]
        attempt[f"raw_{phase}"] = evidence
        current.save()

    try:
        result["stage"] = "resetting"
        current.save()
        deploy.reset(cluster)
        result["stage"] = "preparing"
        current.save()
        ids = run._prepare(cluster, current, profile, "fake", manifest["api_image"]["tag"])
        result["ids"] = ids
        # _prepare saves the selected node image chain in api_image_provenance.
        current.save()
        run.capture_idle_db(current, profile)
        phases_before, phases_started = cluster.db_counters(), time.monotonic()
        result["stage"] = "running"
        current.save()
        loose_plan = {**plan, "thresholds": {**plan["thresholds"], "failure_upper": 1.0}}
        step = search.run_step(
            cluster, ids, RATE_PER_S, loose_plan, declared["seed"], profile["capacity"]["disk"],
            window_s=MEASURED_WINDOW_S, confirm=True, on_event=observe,
            cancel_driver=True, checkpoint=checkpoint,
        )
        result["all_phases_db_window"] = {
            "label": "warmup+measure+drain",
            **run.stats.db_delta(phases_before, cluster.db_counters(), time.monotonic() - phases_started),
        }
        step["reliability_status"] = "pending_pooled"
        result.update({"stage": "complete", "step": step, "status": _segment_status(step)})
        current.save()
        current.snapshot("settled")
    except BaseException as error:
        failure = error
        result.update({"stage": "exception", "status": "invalid", "error": f"{type(error).__name__}: {error}"})
    finally:
        try:
            deploy.reset(cluster)
            result["cleanup"] = {"verified": True}
        except BaseException as error:
            result["cleanup"] = {"verified": False, "error": f"{type(error).__name__}: {error}"}
            result["status"] = "invalid"
            if failure is None:
                failure = error
        result["finished_at"] = _utc_now()
        current.save()
        _write_hash_create(output, output.read_bytes())
    if failure is not None:
        raise failure
    return output


def run_preflight(manifest_path: Path) -> Path:
    """Run the one manifest-bound correctness prefix before any hour-long segment."""
    import run

    manifest, manifest_hash = load_manifest(manifest_path)
    output = _preflight_path(manifest_path)
    if output.exists() or _hash_path(output).exists():
        raise ValueError("campaign preflight is already started and cannot be replaced")
    cluster = Cluster()
    cluster.verify()
    profile, plan = _verify_frozen_inputs(cluster, manifest)
    result = {
        "kind": "mixed-confirmation-preflight",
        "status": "partial",
        "stage": "declared",
        "campaign_manifest_path": str(manifest_path.resolve()),
        "campaign_manifest_sha256": manifest_hash,
        "profile_name": manifest["profile_name"],
        "resolved_profile_sha256": manifest["resolved_profile_sha256"],
        "api_image": manifest["api_image"],
        "mix": manifest["mix"],
        "rate_per_s": RATE_PER_S,
        "started_at": _utc_now(),
    }
    current = _SegmentRun(cluster, output, result)
    _atomic_create(output, json.dumps(jsoncodec.encode(result), indent=2, allow_nan=False).encode("utf-8"))
    failure: BaseException | None = None
    try:
        result["stage"] = "resetting"
        current.save()
        deploy.reset(cluster)
        result["stage"] = "preparing"
        current.save()
        ids = run._prepare(cluster, current, profile, "fake", manifest["api_image"]["tag"])
        result["ids"] = ids
        result["stage"] = "running"
        current.save()

        def save_progress(progress: dict) -> None:
            result["preflight"] = progress
            current.save()

        candidate = preflight.run(
            cluster, ids, RATE_PER_S, plan, manifest_path.parent / "preflight-logs", save_progress,
        )
        result.update({"stage": "complete", "preflight": candidate, "status": candidate["overall"]})
        current.save()
    except BaseException as error:
        failure = error
        result.update({"stage": "exception", "status": "invalid", "error": f"{type(error).__name__}: {error}"})
    finally:
        try:
            deploy.reset(cluster)
            result["cleanup"] = {"verified": True}
        except BaseException as error:
            result["cleanup"] = {"verified": False, "error": f"{type(error).__name__}: {error}"}
            result["status"] = "invalid"
            if failure is None:
                failure = error
        result["finished_at"] = _utc_now()
        current.save()
        _write_hash_create(output, output.read_bytes())
    if failure is not None:
        raise failure
    return output


def _require_passing_preflight(manifest_path: Path, manifest_hash: str) -> None:
    path = _preflight_path(manifest_path)
    try:
        _passing_preflight(path, manifest_hash)
    except (OSError, ValueError) as error:
        raise ValueError("campaign requires a completed manifest-bound preflight") from error


def _passing_preflight(path: Path, manifest_hash: str) -> tuple[dict, str]:
    result, digest = _result_with_hash(path)
    if (
        result.get("kind") != "mixed-confirmation-preflight"
        or result.get("campaign_manifest_sha256") != manifest_hash
        or result.get("status") != "pass"
        or result.get("preflight", {}).get("overall") != "pass"
        or type(result.get("cleanup")) is not dict
        or result["cleanup"].get("verified") is not True
    ):
        raise ValueError("campaign requires a successful manifest-bound preflight with verified cleanup")
    return result, digest


def _result_with_hash(path: Path) -> tuple[dict, str]:
    contents = _verify_hash(path)
    result = jsoncodec.decode(_json_object(contents, path))
    if type(result) is not dict:
        raise ValueError(f"expected benchmark result object in {path}")
    return result, _sha256_bytes(contents)


def aggregate(manifest_path: Path) -> dict:
    """Read-only fixed-five aggregate; it never creates or repeats a segment."""
    try:
        manifest, manifest_hash = load_manifest(manifest_path)
    except (OSError, ValueError) as error:
        return {
            "kind": "mixed-confirmation-campaign",
            "overall": "invalid",
            "error": f"{type(error).__name__}: {error}",
        }

    try:
        preflight_result, preflight_digest = _passing_preflight(_preflight_path(manifest_path), manifest_hash)
        preflight = {"status": "pass", "sha256": preflight_digest, "result": preflight_result}
    except (OSError, ValueError) as error:
        preflight = {"status": "invalid", "error": f"{type(error).__name__}: {error}"}
    expected = {Path(segment["output_path"]) for segment in manifest["segments"]}
    extras = sorted(str(path) for path in manifest_path.parent.glob("segments/segment-*.json") if path not in expected)
    segments: list[dict] = []
    invalid = bool(extras) or preflight["status"] != "pass"
    incomplete = False
    seen_ids: set[str] = set()
    for declared in manifest["segments"]:
        path = Path(declared["output_path"])
        if not path.exists() or not _hash_path(path).exists():
            segments.append({
                "index": declared["index"],
                "seed": declared["seed"],
                "status": "missing",
                "output_path": str(path),
            })
            incomplete = True
            continue
        try:
            result, digest = _result_with_hash(path)
        except (OSError, ValueError) as error:
            segments.append({
                "index": declared["index"],
                "seed": declared["seed"],
                "status": "invalid",
                "error": f"{type(error).__name__}: {error}",
            })
            invalid = True
            continue
        status = result.get("status")
        step = result.get("step")
        valid_identity = (
            result.get("campaign_manifest_sha256") == manifest_hash
            and result.get("index") == declared["index"]
            and result.get("seed") == declared["seed"]
            and status in SEGMENT_STATUSES
            and type(result.get("cleanup")) is dict
            and result["cleanup"].get("verified") is True
        )
        validation_error = None
        if status in {"pass", "fail", "inconclusive"}:
            step_valid, validation_error = _valid_step(
                step, declared["seed"], manifest["eligible_classes"], manifest["deadlines_s"], seen_ids,
            )
            if type(step) is not dict or status != step.get("overall"):
                step_valid, validation_error = False, "segment status does not match its step verdict"
            valid_identity = valid_identity and step_valid
        if not valid_identity:
            invalid = True
        segments.append({
            "index": declared["index"], "seed": declared["seed"], "status": status if valid_identity else "invalid",
            "output_path": str(path), "sha256": digest, "step": step, "validation_error": validation_error,
        })

    pooled = _pooled_reliability(manifest, segments)
    statuses = [segment["status"] for segment in segments]
    if invalid or "invalid" in statuses or any(item["status"] == "invalid" for item in pooled.values()):
        overall = "invalid"
    elif "fail" in statuses or any(item["status"] == "fail" for item in pooled.values()):
        overall = "fail"
    elif (
        incomplete
        or "partial" in statuses
        or "inconclusive" in statuses
        or any(item["status"] == "inconclusive" for item in pooled.values())
    ):
        overall = "inconclusive"
    else:
        overall = "pass"
    return {
        "kind": "mixed-confirmation-campaign", "campaign_manifest": manifest,
        "campaign_manifest_sha256": manifest_hash, "preflight": preflight,
        "segments": segments, "extra_segment_paths": extras,
        "pooled_reliability": pooled, "overall": overall,
    }


def _valid_step(
    step: object, seed: int, eligible_classes: list[str], deadlines: dict, seen_ids: set[str],
) -> tuple[bool, str | None]:
    """Prove a completed step retains the raw identities and every gate result."""
    if type(step) is not dict:
        return False, "step is not an object"
    if (
        step.get("seed") != seed
        or step.get("rate") != RATE_PER_S
        or step.get("window_s") != MEASURED_WINDOW_S
        or step.get("confirm") is not True
        or step.get("window") != [WARMUP_S, WARMUP_S + MEASURED_WINDOW_S]
        or not _finite_number(step.get("started"))
    ):
        return False, "step identity or measured window does not match the campaign"
    sent, records, outcomes = step.get("sent"), step.get("records"), step.get("outcomes")
    if type(sent) is not list or type(records) is not list or type(outcomes) is not dict:
        return False, "step is missing raw sent, records, or durable outcomes"
    if not all(type(item) is dict for item in sent + records):
        return False, "step raw sent or record entry is not an object"
    sent_ids = [item.get("id") for item in sent]
    record_ids = [item.get("id") for item in records]
    if (
        any(type(request_id) is not str or not request_id for request_id in sent_ids)
        or len(sent_ids) != len(set(sent_ids))
        or len(records) != len(sent_ids)
        or len(record_ids) != len(set(record_ids))
        or set(sent_ids) != set(record_ids)
        or any(request_id in seen_ids for request_id in sent_ids)
    ):
        return False, "step request IDs are malformed, duplicated, or reused across segments"
    sent_by_id = {item["id"]: item for item in sent}
    if any(record.get("class") != sent_by_id[record["id"]].get("class") for record in records):
        return False, "step records do not match raw sent identities"
    if not _valid_outcomes(outcomes, set(sent_ids), records, step):
        return False, "step durable outcomes are incomplete or do not match raw records"
    if records != search.to_records(sent, outcomes, step["started"], deadlines):
        return False, "step records do not reproduce from raw sent and durable outcomes"
    if not _valid_gates(step, eligible_classes):
        return False, "step does not retain consistent non-reliability gate and validity evidence"
    seen_ids.update(sent_ids)
    return True, None


def _finite_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _valid_outcomes(outcomes: dict, sent_ids: set[str], records: list[dict], step: dict) -> bool:
    durable = outcomes.get("outcomes")
    duplicates = outcomes.get("duplicates")
    missing = outcomes.get("missing")
    rows = outcomes.get("rows")
    if not (
        type(durable) is dict
        and type(duplicates) is dict
        and type(missing) is list
        and type(rows) is list
        and set(durable).issubset(sent_ids)
        and all(type(value) is dict for value in durable.values())
        and all(value.get("request_id") == request_id for request_id, value in durable.items())
        and all(type(request_id) is str for request_id in missing)
        and step.get("missing") == missing
        and type(step.get("faults")) is dict
        and step["faults"].get("duplicate_executions") == len(duplicates)
    ):
        return False
    if not all(type(row) is dict and row.get("request_id") in sent_ids for row in rows):
        return False
    row_counts = Counter(row["request_id"] for row in rows)
    expected_duplicates = {request_id: count for request_id, count in row_counts.items() if count > 1}
    if duplicates != expected_duplicates or set(missing) != sent_ids - set(durable):
        return False
    return all(
        record.get("outcome") in {"correct", "failed", "unfinished"}
        and (record.get("outcome") != "correct" or durable.get(record["id"], {}).get("correct") is True)
        for record in records
    )


def _valid_gates(step: dict, eligible_classes: list[str]) -> bool:
    verdict = step.get("verdict")
    validity = step.get("validity")
    if type(verdict) is not dict or type(validity) is not dict:
        return False
    if (
        type(validity.get("invalid")) is not list
        or type(validity.get("warnings")) is not list
        or type(step.get("backlog")) is not list
        or type(step.get("host")) is not list
        or type(step.get("faults")) is not dict
        or any(type(value) is not int or value < 0 for value in step["faults"].values())
    ):
        return False
    gates = [verdict.get("throughput"), verdict.get("drift"), verdict.get("tenant_faults")]
    latency, reliability = verdict.get("latency"), verdict.get("reliability")
    if type(latency) is not dict or type(reliability) is not dict:
        return False
    if set(latency) != set(eligible_classes) or set(reliability) != set(eligible_classes):
        return False
    for name in eligible_classes:
        class_latency = latency[name]
        if type(class_latency) is not dict or set(class_latency) != {"queue_p95", "e2e_p95"}:
            return False
        gates.extend(class_latency.values())
        gates.append(reliability[name])
    if not all(
        type(gate) is dict
        and {"status", "value", "limit"}.issubset(gate)
        and gate.get("status") in GATE_STATUSES
        for gate in gates
    ):
        return False
    verdict_statuses = [gate["status"] for gate in gates]
    verdict_overall = (
        "fail" if "fail" in verdict_statuses
        else "inconclusive" if "inconclusive" in verdict_statuses
        else "pass"
    )
    if verdict.get("overall") != verdict_overall:
        return False
    if step.get("overall") == "invalid":
        return bool(validity["invalid"])
    return not validity["invalid"] and step.get("overall") == verdict_overall


def _pooled_reliability(manifest: dict, segments: list[dict]) -> dict[str, dict]:
    pooled: dict[str, list[dict]] = {name: [] for name in manifest["eligible_classes"]}
    invalid_records = {name: 0 for name in pooled}
    completed = {name: 0 for name in pooled}
    for segment in segments:
        step = segment.get("step")
        if not isinstance(step, dict):
            continue
        window = step.get("window")
        records = step.get("records")
        if not isinstance(window, (list, tuple)) or len(window) != 2 or not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or record.get("class") not in pooled:
                continue
            scheduled = record.get("scheduled_s")
            if isinstance(scheduled, (int, float)) and window[0] <= scheduled < window[1]:
                pooled[record["class"]].append(record)
                if record.get("outcome") == "correct" and record.get("sent_s") is None:
                    invalid_records[record["class"]] += 1
                if _durably_completed(record, step):
                    completed[record["class"]] += 1
    evidence = {}
    for name, records in pooled.items():
        observations = len(records)
        failures = sum(record.get("outcome") != "correct" for record in records)
        upper = capacity.clopper_pearson_upper(failures, observations) if observations else None
        if invalid_records[name]:
            status = "invalid"
        elif observations < RELIABILITY_OBSERVATIONS:
            status = "inconclusive"
        else:
            status, _ = capacity.reliability_evidence(failures, observations, RELIABILITY_LIMIT)
            if status == "insufficient":
                status = "inconclusive"
        evidence[name] = {
            "observations": observations,
            "submitted": sum(record.get("sent_s") is not None for record in records),
            "accepted": sum(_http_accepted(record) for record in records),
            "completed": completed[name],
            "failures": failures,
            "invalid_records": invalid_records[name],
            "upper_95": upper,
            "limit": RELIABILITY_LIMIT, "status": status,
        }
    return evidence


def _http_accepted(record: dict) -> bool:
    status = record.get("http_status")
    return type(status) is int and 200 <= status < 300


def _durably_completed(record: dict, step: dict) -> bool:
    outcomes = step.get("outcomes")
    if type(outcomes) is dict and type(outcomes.get("outcomes")) is dict:
        durable = outcomes["outcomes"].get(record.get("id"))
        if type(durable) is dict:
            return durable.get("completed") is not None
    return record.get("completed_s") is not None
