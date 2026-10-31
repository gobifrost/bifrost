"""V2 browser diagnostic inputs, provenance, and event validation.

This module deliberately prepares source and validates browser evidence only.
The benchmark harness owns the normal application.deploy job, browser-runner
Pod lifecycle, and backend durable-record reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
FIXTURE_ROOT = HERE / "browser" / "fixture"
DRIVER_PATH = HERE / "browser" / "driver.mjs"
VARIANTS = ("control", "same_body_graph", "shallow_list", "detail_hydration")
PLAYWRIGHT_VERSION = "1.64.1"
BROWSER_RUNNER_IMAGE = f"mcr.microsoft.com/playwright:v{PLAYWRIGHT_VERSION}-noble"
TERMINAL_STATUSES = frozenset({"Success", "Failed", "CompletedWithErrors", "Timeout", "Cancelled"})
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def source_files(root: Path = FIXTURE_ROOT) -> dict[str, bytes]:
    """Read only normalized Vite source files, never a built dist or secrets."""
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        parsed = PurePosixPath(relative)
        if relative.startswith(".") or "node_modules" in parsed.parts or "dist" in parsed.parts:
            continue
        files[relative] = path.read_bytes()
    required = {"package.json", "index.html", "src/main.tsx", "vite.config.ts"}
    missing = sorted(required - set(files))
    if missing:
        raise ValueError(f"browser fixture is missing required source: {', '.join(missing)}")
    return files


def source_manifest(files: dict[str, bytes]) -> dict:
    digest = hashlib.sha256()
    entries: list[dict] = []
    for path in sorted(files):
        content = files[path]
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(content)
        entries.append({"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    return {"file_count": len(entries), "source_bytes": sum(item["bytes"] for item in entries), "source_sha256": digest.hexdigest(), "files": entries}


def write_source_zip(destination: Path, files: dict[str, bytes]) -> dict:
    """Create deterministic source input for the supported application.deploy route."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path)
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[path])
    return {**source_manifest(files), "archive_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "archive_bytes": destination.stat().st_size}


def build_receipt(source: dict, accepted: dict, job: dict, application: dict) -> dict:
    """Keep only deployment/build provenance needed to interpret browser timing."""
    required_job = {
        "id",
        "job_type",
        "status",
        "execution_backend",
        "created_at",
        "started_at",
        "completed_at",
    }
    if not required_job <= set(job) or job["status"] != "succeeded":
        raise ValueError("application.deploy did not reach succeeded")
    if job["job_type"] != "application.deploy":
        raise ValueError("receipt requires an application.deploy platform job")
    accepted_job_id = accepted.get("job_id")
    if not accepted_job_id or str(accepted_job_id) != str(job["id"]):
        raise ValueError("application.deploy acceptance and PlatformJob IDs differ")
    fields = (
        "sdk_package_version",
        "sdk_fingerprint",
        "sdk_contract_version",
        "sdk_binding_api_major",
        "sdk_built_at",
        "active_deployment_id",
    )
    if any(application.get(field) in (None, "") for field in fields):
        raise ValueError("deployed app is missing SDK or deployment provenance")
    return {
        "source": {key: source[key] for key in ("file_count", "source_bytes", "source_sha256", "archive_sha256", "archive_bytes")},
        "deploy": {
            "job_id": str(accepted_job_id),
            "status": job["status"],
            "active_deployment_id": application["active_deployment_id"],
        },
        "build": {
            "execution_backend": job["execution_backend"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            "memory_required_bytes": job.get("memory_required_bytes"),
            "memory_start_bytes": job.get("memory_start_bytes"),
            "memory_peak_bytes": job.get("memory_peak_bytes"),
            "memory_limit_bytes": job.get("memory_limit_bytes"),
        },
        "sdk": {field: application[field] for field in fields[:-1]},
        "browser_driver_sha256": hashlib.sha256(DRIVER_PATH.read_bytes()).hexdigest(),
    }


def runner_receipt(image: str, image_id: str, cached: bool) -> dict:
    """Require immutable browser image provenance before opening a measured page."""
    prefix = f"mcr.microsoft.com/playwright:v{PLAYWRIGHT_VERSION}-noble@"
    if not image.startswith(prefix) or not _DIGEST.fullmatch(image.removeprefix(prefix)):
        raise ValueError("browser runner must use the pinned Playwright image digest")
    if not _DIGEST.fullmatch(image_id):
        raise ValueError("browser runner image ID is not a digest")
    if cached is not True:
        raise ValueError("browser runner image must be loaded before measurement")
    return {"image": image, "image_id": image_id, "playwright_version": PLAYWRIGHT_VERSION, "cached_before_measurement": cached}


def validate_events(events: list[dict]) -> dict:
    """Classify one page's observed SDK transport without inventing a path.

    ``useWorkflow`` legitimately settles directly from a terminal POST, from
    its immediate GET after subscription, or from a terminal stream event and
    the SDK's terminal GET.  The observer records those native operations, but
    never adds a request or requires one a fast path did not make.
    """
    if not isinstance(events, list) or not events:
        raise ValueError("browser run has no events")
    types = [event.get("type") for event in events]
    positions: dict[str, int] = {}
    for event_type in ("invoke", "post"):
        if types.count(event_type) != 1:
            raise ValueError(f"browser run needs exactly one {event_type} event")
        positions[event_type] = types.index(event_type)
    if positions["invoke"] > positions["post"]:
        raise ValueError("browser invocation did not precede the workflow POST")
    if not all(isinstance(event.get("t_ms"), (int, float)) and event["t_ms"] >= 0 for event in events):
        raise ValueError("browser events need nonnegative performance timestamps")
    if any(events[index]["t_ms"] > events[index + 1]["t_ms"] for index in range(len(events) - 1)):
        raise ValueError("browser events are not in same-clock order")
    post = events[positions["post"]]
    execution_id = post.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise ValueError("browser events need one execution ID and nonnegative performance timestamps")
    if any(
        event.get("execution_id") not in (None, execution_id)
        for event in events[positions["post"] + 1 :]
    ):
        raise ValueError("browser transport events are not correlated to one execution")
    safe_keys = {"type", "t_ms", "execution_id", "status", "method", "path", "channel"}
    if any(set(event) - safe_keys for event in events):
        raise ValueError("browser event includes unsafe payload context")
    post_terminal = post.get("status") in TERMINAL_STATUSES
    stream_terminal = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "terminal" and event.get("status") in TERMINAL_STATUSES
    ]
    terminal_gets = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "get" and event.get("status") in TERMINAL_STATUSES
    ]
    subscriptions = [index for index, event in enumerate(events) if event.get("type") == "subscribe"]
    if post_terminal:
        path_label = "inline_post"
    elif stream_terminal:
        terminal_index = stream_terminal[-1]
        if len(stream_terminal) != 1 or len(subscriptions) != 1:
            raise ValueError("streamed workflow needs one observed subscription and terminal frame")
        if not (positions["post"] < subscriptions[0] < terminal_index):
            raise ValueError("streamed workflow events are not POST, subscribe, terminal ordered")
        if len(terminal_gets) != 1 or terminal_gets[0] <= terminal_index:
            raise ValueError("streamed workflow terminal frame was not followed by the SDK detail GET")
        path_label = "streamed_ws"
    elif terminal_gets:
        if len(terminal_gets) != 1:
            raise ValueError("immediate SDK read observed more than one terminal GET")
        path_label = "immediate_get"
    else:
        raise ValueError("browser run did not observe a terminal SDK transport path")
    renders = [index for index, event in enumerate(events) if event.get("type") == "render"]
    if renders and renders[-1] < positions["post"]:
        raise ValueError("browser render preceded the workflow POST")
    return {
        "execution_id": execution_id,
        "transport_path": path_label,
        "events": events,
        "browser_wall_ms": events[-1]["t_ms"] - events[0]["t_ms"],
    }


def decode_driver_output(text: str) -> list[dict]:
    """Read one JSON line from the browser runner without accepting log noise."""
    lines = [line for line in text.splitlines() if line.startswith("{")]
    if len(lines) != 1:
        raise ValueError("browser runner did not emit exactly one JSON result")
    result = json.loads(lines[0])
    if not isinstance(result, dict) or not isinstance(result.get("runs"), list):
        raise ValueError("browser runner result has no runs")
    return result["runs"]
