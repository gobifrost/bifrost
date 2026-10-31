"""Public synthetic interactive-integration fixture for benchmark runs.

The fixture models a Halo-like portal action without customer data, credentials,
or a live provider.  It deliberately keeps the integration lookup on the normal
SDK path and sends only generated benchmark request IDs to the in-cluster fake
service.  Results contain monotonic component spans; provider spans are read
from the fake service and correlated by that request ID.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath

import import_diagnostic

WORKLOAD_VERSION = "interactive-integration-v1"
INTEGRATION_NAME = "Bench Synthetic Halo"
SITE_COUNT = 88
DETAIL_FANOUT = 8
FAKE_API_URL = "http://bench-fake-api.bench-infra:8080"
CAPTURED_FILE_COUNT = 20
CAPTURED_SOURCE_BYTES = 411_390

WORKFLOW_NAMES = {
    "control": "bench_wf_interactive_control",
    "same_body_graph": "bench_wf_interactive_same_body_graph",
    "shallow_list": "bench_wf_interactive_shallow_list",
    "detail_hydration": "bench_wf_interactive_detail_hydration",
}
CLASS_VARIANTS = {
    "wf-interactive-control": "control",
    "wf-interactive-same-body-graph": "same_body_graph",
    "wf-interactive-shallow-list": "shallow_list",
    "wf-interactive-detail-hydration": "detail_hydration",
}

CONTROL_WORKFLOW_SOURCE = '''import time

from bifrost import workflow


@workflow(name="bench_wf_interactive_control", description="Synthetic interactive fixture control without shared imports or provider reads.")
async def bench_wf_interactive_control(bench_request_id: str = "") -> dict:
    started = time.monotonic()
    return {
        "ok": True,
        "variant": "control",
        "request_id": bench_request_id,
        "workload_version": "interactive-integration-v1",
        "site_count": 0,
        "detail_count": 0,
        "workflow_spans": {"workflow_wall_ms": round((time.monotonic() - started) * 1000, 3), "sdk_mapping_wall_ms": 0.0, "provider_wall_ms": 0.0},
    }
'''

SHARED_WORKFLOW_SOURCE = '''from bifrost import workflow

from bench_interactive_fixture.shared.workload import run


@workflow(name="bench_wf_interactive_same_body_graph", description="Synthetic interactive fixture with the shared graph and no provider reads.")
async def bench_wf_interactive_same_body_graph(bench_request_id: str = "") -> dict:
    return await run("same_body_graph", bench_request_id)


@workflow(name="bench_wf_interactive_shallow_list", description="Synthetic interactive fixture with one 88-site provider list read.")
async def bench_wf_interactive_shallow_list(bench_request_id: str = "") -> dict:
    return await run("shallow_list", bench_request_id)


@workflow(name="bench_wf_interactive_detail_hydration", description="Synthetic interactive fixture with one list plus bounded 88-site detail reads.")
async def bench_wf_interactive_detail_hydration(bench_request_id: str = "") -> dict:
    return await run("detail_hydration", bench_request_id)
'''

SHARED_INIT_SOURCE = '"""Shared public modules for the synthetic interactive benchmark fixture."""\n'

AUTHORIZATION_SOURCE = '''from bifrost import integrations


async def resolve_mapping():
    integration = await integrations.get("Bench Synthetic Halo")
    if integration is None:
        raise RuntimeError("synthetic benchmark integration is unavailable")
    endpoint = integration.config.get("base_url")
    if not isinstance(endpoint, str) or not endpoint.startswith("http://bench-fake-api."):
        raise RuntimeError("synthetic benchmark integration endpoint is invalid")
    return endpoint.rstrip("/")
'''

CLIENT_SOURCE = '''import httpx


async def get_json(endpoint, path, params):
    # Each provider read deliberately creates a fresh client, matching the
    # portal shape while keeping the target in-cluster and credential-free.
    async with httpx.AsyncClient(base_url=endpoint, timeout=30.0) as client:
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()
'''

WORKLOAD_SOURCE = '''import asyncio
import time

from bench_import_fixture.graph.entry import facade
from bench_interactive_fixture.catalog.entry import CATALOG_TOKEN
from bench_interactive_fixture.shared.authorization import resolve_mapping
from bench_interactive_fixture.shared.client import get_json

SITE_COUNT = 88
DETAIL_FANOUT = 8
LIST_DELAY_MS = 6
DETAIL_DELAY_MS = 3
LIST_PAYLOAD_BYTES = 4096
DETAIL_PAYLOAD_BYTES = 1024


def _spans(started, mapping_started, provider_started):
    now = time.monotonic()
    return {
        "workflow_wall_ms": round((now - started) * 1000, 3),
        "sdk_mapping_wall_ms": round((provider_started - mapping_started) * 1000, 3),
        "provider_wall_ms": round((now - provider_started) * 1000, 3),
    }


async def run(variant, request_id):
    if not request_id:
        raise ValueError("bench_request_id is required")
    started = time.monotonic()
    mapping_started = time.monotonic()
    endpoint = await resolve_mapping()
    # Reuse the existing synthetic import graph in every shared-body case.
    facade.new_request()
    if not CATALOG_TOKEN:
        raise RuntimeError("synthetic benchmark catalog was not imported")
    provider_started = time.monotonic()
    result = {
        "ok": True,
        "variant": variant,
        "request_id": request_id,
        "workload_version": "interactive-integration-v1",
        "site_count": 0,
        "detail_count": 0,
    }
    if variant == "same_body_graph":
        result["workflow_spans"] = _spans(started, mapping_started, provider_started)
        return result
    sites = (await get_json(endpoint, "/integration/sites", {
        "tag": request_id, "delay_ms": LIST_DELAY_MS,
        "payload_bytes": LIST_PAYLOAD_BYTES, "kind": "sites",
    }))["items"]
    if len(sites) != SITE_COUNT:
        raise RuntimeError("synthetic site list did not contain 88 items")
    result["site_count"] = len(sites)
    if variant == "shallow_list":
        result["workflow_spans"] = _spans(started, mapping_started, provider_started)
        return result
    semaphore = asyncio.Semaphore(DETAIL_FANOUT)

    async def detail(site):
        async with semaphore:
            return await get_json(endpoint, f"/integration/sites/{site['id']}", {
                "tag": request_id, "delay_ms": DETAIL_DELAY_MS,
                "payload_bytes": DETAIL_PAYLOAD_BYTES, "kind": "site_detail",
            })

    details = await asyncio.gather(*(detail(site) for site in sites))
    result["detail_count"] = len(details)
    result["workflow_spans"] = _spans(started, mapping_started, provider_started)
    return result
'''

CATALOG_ENTRY_SOURCE = '''from bench_interactive_fixture.catalog.module_01 import TOKEN_01
from bench_interactive_fixture.catalog.module_02 import TOKEN_02
from bench_interactive_fixture.catalog.module_03 import TOKEN_03
from bench_interactive_fixture.catalog.module_04 import TOKEN_04
from bench_interactive_fixture.catalog.module_05 import TOKEN_05
from bench_interactive_fixture.catalog.module_06 import TOKEN_06
from bench_interactive_fixture.catalog.module_07 import TOKEN_07
from bench_interactive_fixture.catalog.module_08 import TOKEN_08
from bench_interactive_fixture.catalog.module_09 import TOKEN_09

CATALOG_TOKEN = "|".join((TOKEN_01, TOKEN_02, TOKEN_03, TOKEN_04, TOKEN_05, TOKEN_06, TOKEN_07, TOKEN_08, TOKEN_09))
'''


def _catalog_module(index: int, padding: int = 0) -> str:
    """Stable-library modules make the public graph breadth and bytes explicit."""
    return f'''import json
from urllib.parse import quote

MODULE = {index}
PAYLOAD = "{'x' * padding}"
TOKEN_{index:02d} = quote(json.dumps({{"module": MODULE, "bytes": len(PAYLOAD)}}))
'''

_MANIFEST_KEYS = {"workload_version", "files", "file_count", "source_bytes", "source_sha256"}
_WORKFLOW_PATHS = {
    "workflows/bench_interactive_control.py",
    "workflows/bench_interactive_shared.py",
}


def fixture_files() -> dict[str, str]:
    """Return the source graph written through the ordinary editor API."""
    reused = {
        path: source
        for path, source in import_diagnostic.fixture_files().items()
        if not path.startswith("workflows/")
    }
    files = {
        **reused,
        "workflows/bench_interactive_control.py": CONTROL_WORKFLOW_SOURCE,
        "workflows/bench_interactive_shared.py": SHARED_WORKFLOW_SOURCE,
        "bench_interactive_fixture/shared/__init__.py": SHARED_INIT_SOURCE,
        "bench_interactive_fixture/shared/authorization.py": AUTHORIZATION_SOURCE,
        "bench_interactive_fixture/shared/client.py": CLIENT_SOURCE,
        "bench_interactive_fixture/shared/workload.py": WORKLOAD_SOURCE,
        "bench_interactive_fixture/catalog/entry.py": CATALOG_ENTRY_SOURCE,
    }
    for index in range(1, 10):
        files[f"bench_interactive_fixture/catalog/module_{index:02d}.py"] = _catalog_module(index)
    missing = CAPTURED_SOURCE_BYTES - _evidence(files)["source_bytes"]
    if missing < 0:
        raise RuntimeError("interactive fixture source exceeds captured byte budget")
    for index in range(1, 10):
        padding, remainder = divmod(missing, 9)
        files[f"bench_interactive_fixture/catalog/module_{index:02d}.py"] = _catalog_module(index, padding + (1 if index <= remainder else 0))
    if len(files) != CAPTURED_FILE_COUNT or _evidence(files)["source_bytes"] != CAPTURED_SOURCE_BYTES:
        raise RuntimeError("interactive fixture no longer matches the captured source shape")
    return files


def _evidence(files: dict[str, str]) -> dict:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[path].encode("utf-8"))
    return {
        "file_count": len(files),
        "source_bytes": sum(len(source.encode("utf-8")) for source in files.values()),
        "source_sha256": digest.hexdigest(),
    }


def fixture_manifest() -> dict:
    files = fixture_files()
    return {"workload_version": WORKLOAD_VERSION, "files": files, **_evidence(files)}


def fixture_metadata(fixture: dict) -> dict:
    return {key: fixture[key] for key in ("workload_version", "file_count", "source_bytes", "source_sha256")}


def validate_fixture_manifest(manifest: object) -> dict:
    """Reject unsafe or incomplete public source before it reaches the editor."""
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise ValueError("interactive fixture must have exactly the canonical manifest fields")
    if manifest["workload_version"] != WORKLOAD_VERSION:
        raise ValueError("interactive fixture workload version is not supported")
    files = manifest["files"]
    if not isinstance(files, dict) or not _WORKFLOW_PATHS.issubset(files):
        raise ValueError("interactive fixture must contain every workflow wrapper")
    normalized: dict[str, str] = {}
    for path, source in files.items():
        parsed = PurePosixPath(path) if isinstance(path, str) else None
        if (not isinstance(path, str) or not path or "\\" in path or not path.endswith(".py")
                or parsed.is_absolute() or parsed.as_posix() != path or any(part in {".", ".."} for part in parsed.parts)):
            raise ValueError("interactive fixture has an unsafe or non-normalized Python path")
        if not isinstance(source, str) or not source:
            raise ValueError("interactive fixture file contents must be nonempty strings")
        normalized[path] = source
    evidence = _evidence(normalized)
    if any(manifest[key] != value for key, value in evidence.items()):
        raise ValueError("interactive fixture evidence does not match its files")
    return {"workload_version": WORKLOAD_VERSION, "files": normalized, **evidence}


def load_fixture_manifest(path: Path) -> dict:
    try:
        return validate_fixture_manifest(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read interactive fixture manifest: {error}") from error


def expected_provider_counts(variant: str) -> dict[str, int]:
    if variant in {"control", "same_body_graph"}:
        return {"sites": 0, "site_detail": 0}
    if variant == "shallow_list":
        return {"sites": 1, "site_detail": 0}
    if variant == "detail_hydration":
        return {"sites": 1, "site_detail": SITE_COUNT}
    raise ValueError(f"unknown interactive fixture variant: {variant}")


def validate_execution_records(records: list[dict], variant: str) -> dict:
    """Require complete, non-sensitive result evidence for each interactive run."""
    failures: list[str] = []
    request_ids: dict[str, str] = {}
    for record in records:
        result = record.get("result")
        valid = (
            record.get("status") == "Success" and isinstance(result, dict)
            and result.get("ok") is True and result.get("variant") == variant
            and result.get("workload_version") == WORKLOAD_VERSION
            and isinstance(result.get("request_id"), str) and bool(result["request_id"])
            and result.get("site_count") == (SITE_COUNT if variant in {"shallow_list", "detail_hydration"} else 0)
            and result.get("detail_count") == (SITE_COUNT if variant == "detail_hydration" else 0)
            and isinstance(result.get("workflow_spans"), dict)
            and set(result["workflow_spans"]) == {"workflow_wall_ms", "sdk_mapping_wall_ms", "provider_wall_ms"}
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
                    for value in result["workflow_spans"].values())
        )
        if not valid:
            failures.append(str(record.get("id", "unknown")))
        else:
            request_ids[str(record["id"])] = result["request_id"]
    return {"overall": "pass" if not failures else "invalid", "failures": failures, "request_ids": request_ids, "records": records}


def correlate_provider_spans(records: list[dict], spans: list[dict], variant: str) -> dict:
    """Check fake-service spans using generated IDs, never caller or source context."""
    expected = expected_provider_counts(variant)
    validation = validate_execution_records(records, variant)
    request_ids = set(validation["request_ids"].values())
    grouped = {request_id: {kind: 0 for kind in expected} for request_id in request_ids}
    invalid: list[dict] = []
    for span in spans:
        tag, kind = span.get("tag"), span.get("kind")
        if tag in grouped and kind in expected:
            grouped[tag][kind] += 1
        elif tag in request_ids:
            invalid.append(span)
    missing = [request_id for request_id, counts in grouped.items() if counts != expected]
    for request_id, counts in grouped.items():
        if any(counts[kind] > expected[kind] for kind in expected):
            invalid.extend(span for span in spans if span.get("tag") == request_id and span.get("kind") in expected)
    per_execution = {
        execution_id: {
            "request_id": request_id,
            "workflow_spans": next(record["result"]["workflow_spans"] for record in records if str(record.get("id")) == execution_id),
            "provider_counts": grouped[request_id],
        }
        for execution_id, request_id in validation["request_ids"].items()
    }
    return {
        "overall": "pass" if validation["overall"] == "pass" and not missing and not invalid else "invalid",
        "expected_provider_counts": expected,
        "provider_counts": grouped,
        "per_execution": per_execution,
        "missing_request_ids": sorted(missing),
        "invalid_spans": invalid,
    }
