"""Synthetic import fixture and strict worker trace evidence for import diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path, PurePosixPath

FIXTURE_VERSION = "import-pair-v1"
TRACE_PREFIX = "bench.import_trace "
PHASES = ("scope", "freshness", "entry_source_fetch", "entry_load_compile_exec", "engine_call")
KINDS = ("module", "package", "namespace", "not_found", "other")


EXPECTED_OUTPUT = {
    "ok": True,
    "endpoint": "https://imports.invalid/fixture",
    "signature": "import-fixture-v1",
}

CONTROL_WORKFLOW_SOURCE = '''from bifrost import workflow


async def _import_body(facade):
    request = facade.new_request()
    endpoint = facade.select_endpoint(request)
    return {"ok": True, "endpoint": endpoint, "signature": facade.signature}


class _ControlFacade:
    signature = "import-fixture-v1"

    @staticmethod
    def new_request():
        return _ControlRequest()

    @staticmethod
    def select_endpoint(request):
        return request.url


class _ControlRequest:
    url = "https://imports.invalid/fixture"


@workflow(name="bench_wf_import_control", description="Import diagnostic control with an inline facade.")
async def bench_wf_import_control() -> dict:
    return await _import_body(_ControlFacade())
'''


GRAPH_WORKFLOW_SOURCE = '''from bifrost import workflow

from bench_import_fixture.graph.entry import facade


async def _import_body(facade):
    request = facade.new_request()
    endpoint = facade.select_endpoint(request)
    return {"ok": True, "endpoint": endpoint, "signature": facade.signature}


@workflow(name="bench_wf_import_graph", description="Import diagnostic namespace/package graph with a pre-engine facade import.")
async def bench_wf_import_graph() -> dict:
    return await _import_body(facade)
'''

GRAPH_ENTRY_SOURCE = '''from bench_import_fixture.graph.providers import build_facade

facade = build_facade()
'''

GRAPH_INIT_SOURCE = '"""Synthetic import-fixture package below a namespace root."""\n'

GRAPH_PROVIDER_SOURCE = '''import requests

from bench_import_fixture.graph.signature import SIGNATURE


class _GraphFacade:
    signature = SIGNATURE

    @staticmethod
    def new_request():
        return requests.Request("GET", "https://imports.invalid/fixture")

    @staticmethod
    def select_endpoint(request):
        return request.url


def build_facade():
    return _GraphFacade()
'''

GRAPH_SIGNATURE_SOURCE = 'SIGNATURE = "import-fixture-v1"\n'


def fixture_files() -> dict[str, str]:
    """One deliberately small workspace namespace/package branch, with no network I/O."""
    return {
        "workflows/bench_import_control.py": CONTROL_WORKFLOW_SOURCE,
        "workflows/bench_import_graph.py": GRAPH_WORKFLOW_SOURCE,
        "bench_import_fixture/graph/__init__.py": GRAPH_INIT_SOURCE,
        "bench_import_fixture/graph/entry.py": GRAPH_ENTRY_SOURCE,
        "bench_import_fixture/graph/providers.py": GRAPH_PROVIDER_SOURCE,
        "bench_import_fixture/graph/signature.py": GRAPH_SIGNATURE_SOURCE,
    }


_MANIFEST_KEYS = {"version", "files", "file_count", "source_bytes", "source_sha256"}
_REQUIRED_WORKFLOW_PATHS = {
    "workflows/bench_import_control.py",
    "workflows/bench_import_graph.py",
}


def fixture_metadata(fixture: dict) -> dict:
    """The non-source evidence stored in public benchmark artifacts."""
    return {key: fixture[key] for key in ("version", "file_count", "source_bytes", "source_sha256")}


def _fixture_evidence(version: str, files: dict[str, str]) -> dict:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[path].encode("utf-8"))
    return {
        "version": version,
        "file_count": len(files),
        "source_bytes": sum(len(source.encode("utf-8")) for source in files.values()),
        "source_sha256": digest.hexdigest(),
    }


def _python_path(path: object) -> bool:
    if not isinstance(path, str) or not path or "\\" in path:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        return False
    parsed = PurePosixPath(path)
    return (
        not parsed.is_absolute()
        and parsed.as_posix() == path
        and all(part not in {".", ".."} for part in parsed.parts)
        and path.endswith(".py")
    )


def validate_fixture_manifest(manifest: object) -> dict:
    """Validate a private import fixture before it can reach the cluster editor API."""
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise ValueError("import fixture must have exactly the canonical manifest fields")
    version, files = manifest["version"], manifest["files"]
    if not isinstance(version, str) or not version:
        raise ValueError("import fixture version must be a nonempty string")
    if not isinstance(files, dict) or not files:
        raise ValueError("import fixture files must be a nonempty object")
    if not _REQUIRED_WORKFLOW_PATHS.issubset(files):
        raise ValueError("import fixture must contain the control and graph workflow wrappers")
    normalized: dict[str, str] = {}
    for path, source in files.items():
        if not _python_path(path):
            raise ValueError("import fixture has an unsafe or non-normalized Python path")
        if not isinstance(source, str) or not source:
            raise ValueError("import fixture file contents must be nonempty strings")
        normalized[path] = source
    evidence = _fixture_evidence(version, normalized)
    for key in ("file_count", "source_bytes"):
        value = manifest[key]
        if type(value) is not int or value < 0 or value != evidence[key]:
            raise ValueError(f"import fixture {key} does not match its files")
    digest = manifest["source_sha256"]
    if not isinstance(digest, str) or digest != evidence["source_sha256"]:
        raise ValueError("import fixture source_sha256 does not match its files")
    return {"version": version, "files": normalized, **evidence}


def load_fixture_manifest(path: Path) -> dict:
    """Read a private fixture manifest without retaining its source in benchmark artifacts."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read import fixture manifest: {error}") from error
    return validate_fixture_manifest(manifest)


def fixture_manifest() -> dict:
    """Stable fixture evidence stored with setup, never inferred from an image or runtime source."""
    files = fixture_files()
    return {"files": files, **_fixture_evidence(FIXTURE_VERSION, files)}


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _wall(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _counts(value: object, names: tuple[str, ...]) -> bool:
    return isinstance(value, dict) and set(value) == set(names) and all(_count(value[name]) for name in names)


def _walls(value: object, names: tuple[str, ...]) -> bool:
    return isinstance(value, dict) and set(value) == set(names) and all(_wall(value[name]) for name in names)


def validate_trace(payload: object) -> dict | None:
    """Accept only the fixed numeric-only trace schema emitted by the worker."""
    if not isinstance(payload, dict) or set(payload) != {"execution_id", "metadata"}:
        return None
    execution_id, metadata = payload["execution_id"], payload["metadata"]
    if not isinstance(execution_id, str) or not execution_id or not isinstance(metadata, dict):
        return None
    if set(metadata) != {"schema_version", "partial_start", "child_wall_ms", "phase_counts", "phase_wall_ms", "module_resolve", "virtual_import"}:
        return None
    resolver = metadata["module_resolve"]
    virtual = metadata["virtual_import"]
    if not _count(metadata["schema_version"]) or metadata["schema_version"] != 1:
        return None
    if not _count(metadata["partial_start"]) or metadata["partial_start"] != 1 or not _wall(metadata["child_wall_ms"]):
        return None
    if not _counts(metadata["phase_counts"], PHASES) or not _walls(metadata["phase_wall_ms"], PHASES):
        return None
    if not isinstance(resolver, dict) or set(resolver) != {"transport_calls", "transport_wall_ms", "cache_hits", "kind_counts", "kind_wall_ms"}:
        return None
    if not _count(resolver["transport_calls"]) or not _wall(resolver["transport_wall_ms"]) or not _count(resolver["cache_hits"]):
        return None
    if not _counts(resolver["kind_counts"], KINDS) or not _walls(resolver["kind_wall_ms"], KINDS):
        return None
    if not isinstance(virtual, dict) or set(virtual) != {"compile_calls", "compile_wall_ms", "exec_calls", "exec_wall_ms"}:
        return None
    if not all((_count(virtual["compile_calls"]), _wall(virtual["compile_wall_ms"]), _count(virtual["exec_calls"]), _wall(virtual["exec_wall_ms"]))):
        return None
    return payload


def parse_trace_lines(text: str, source: dict) -> tuple[list[dict], list[str]]:
    """Parse every trace line, retaining its worker pod/UID/log path as evidence."""
    traces: list[dict] = []
    invalid: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if TRACE_PREFIX not in line:
            continue
        try:
            payload = json.loads(line.split(TRACE_PREFIX, 1)[1])
        except json.JSONDecodeError:
            invalid.append(f"{source['pod']}:{line_number}: malformed JSON")
            continue
        found = validate_trace(payload)
        if found is None:
            invalid.append(f"{source['pod']}:{line_number}: invalid metadata")
            continue
        traces.append({"source": source, **found})
    return traces, invalid


def capture(cluster, out: Path, name: str, pass_number: int, execution_ids: list[str]) -> dict:
    """Save bounded worker logs immediately and require one valid trace for every raw execution."""
    directory = out / "import-trace-logs"
    directory.mkdir(parents=True, exist_ok=True)
    traces: list[dict] = []
    invalid: list[str] = []
    sources: list[dict] = []
    items = [cluster.api_log_source(), *cluster.worker_log_sources()]
    for item in items:
        source = {"pod": item["pod"], "pod_uid": item["pod_uid"], "container": item["container"]}
        path = directory / f"{name}-pass-{pass_number}-{source['pod']}-{source['pod_uid']}.log"
        path.write_text(item["logs"], encoding="utf-8")
        source["raw_log_path"] = str(path)
        sources.append(source)
        if source["container"] != "worker":
            continue
        parsed, errors = parse_trace_lines(item["logs"], source)
        traces.extend(parsed)
        invalid.extend(errors)
    expected = set(execution_ids)
    counts = {execution_id: 0 for execution_id in expected}
    unmatched: list[str] = []
    for trace in traces:
        execution_id = trace["execution_id"]
        if execution_id in counts:
            counts[execution_id] += 1
        else:
            unmatched.append(execution_id)
    missing = sorted(execution_id for execution_id, count in counts.items() if count == 0)
    duplicates = sorted(execution_id for execution_id, count in counts.items() if count > 1)
    unique_expected = len(execution_ids) == len(expected)
    if not expected:
        invalid.append("no expected execution IDs")
    overall = "pass" if unique_expected and expected and not invalid and not missing and not duplicates else "invalid"
    return {
        "kind": "partial-imports-diagnostic", "overall": overall,
        "expected_execution_ids": execution_ids, "sources": sources, "events": traces,
        "missing_execution_ids": missing, "duplicate_execution_ids": duplicates,
        "unmatched_execution_ids": sorted(unmatched), "invalid_events": invalid,
    }


def validate_execution_records(records: list[dict]) -> dict:
    """Require each recorded import execution to reach Success with the fixture output."""
    checked = []
    for record in records:
        correct = record.get("status") == "Success" and record.get("result") == EXPECTED_OUTPUT
        checked.append({
            "id": record.get("id"), "status": record.get("status"),
            "result": record.get("result"), "correct": correct,
        })
    failures = [record["id"] for record in checked if not record["correct"]]
    return {"overall": "pass" if checked and not failures else "invalid", "records": checked, "failures": failures}


def durable_timing(records: list[dict]) -> dict:
    """Partial worker-duration evidence only; raw nullable engine durations remain in each record."""
    durable: list[float] = []
    engine: list[float] = []
    for record in records:
        started, completed = record.get("started_at"), record.get("completed_at")
        if isinstance(started, str) and isinstance(completed, str):
            durable.append((datetime.fromisoformat(completed.replace("Z", "+00:00")) - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds() * 1000)
        value = record.get("duration_ms")
        if _wall(value):
            engine.append(float(value))

    def summary(values: list[float]) -> dict:
        ordered = sorted(values)
        if not ordered:
            return {"count": 0, "p50_ms": None, "p95_ms": None}
        return {
            "count": len(ordered),
            "p50_ms": ordered[max(0, math.ceil(len(ordered) * 0.5) - 1)],
            "p95_ms": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        }

    return {"durable_started_to_completed_ms": summary(durable), "engine_duration_ms": summary(engine)}
