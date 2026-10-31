"""V2 browser diagnostic inputs, provenance, and event validation.

This module deliberately prepares source and validates browser evidence only.
The benchmark harness owns the normal application.deploy job, browser-runner
Pod lifecycle, and backend durable-record reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
FIXTURE_ROOT = HERE / "browser" / "fixture"
DRIVER_PATH = HERE / "browser" / "driver.mjs"
VARIANTS = ("control", "same_body_graph", "shallow_list", "detail_hydration")
EVENT_TYPES = ("invoke", "post", "subscribe", "terminal", "detail", "render")
PLAYWRIGHT_VERSION = "1.64.1"
BROWSER_RUNNER_IMAGE = f"mcr.microsoft.com/playwright:v{PLAYWRIGHT_VERSION}-noble"


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
    if set(files) < required:
        raise ValueError("browser fixture is not a complete Vite source project")
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
    required_job = {"id", "status"}
    if not required_job <= set(job) or job["status"] != "succeeded":
        raise ValueError("application.deploy did not reach succeeded")
    fields = ("sdk_package_version", "sdk_fingerprint", "sdk_contract_version", "sdk_built_at", "active_deployment_id")
    if any(application.get(field) in (None, "") for field in fields):
        raise ValueError("deployed app is missing SDK or deployment provenance")
    if not accepted.get("job_id"):
        raise ValueError("application.deploy acceptance did not contain job_id")
    return {
        "source": {key: source[key] for key in ("file_count", "source_bytes", "source_sha256", "archive_sha256", "archive_bytes")},
        "deploy": {"job_id": accepted["job_id"], "platform_job_id": job["id"], "status": job["status"], "active_deployment_id": application["active_deployment_id"]},
        "sdk": {field: application[field] for field in fields[:-1]},
        "browser_driver_sha256": hashlib.sha256(DRIVER_PATH.read_bytes()).hexdigest(),
    }


def runner_receipt(image: str, image_id: str, cached: bool) -> dict:
    """Require immutable browser image provenance before opening a measured page."""
    if not image.startswith(f"mcr.microsoft.com/playwright:v{PLAYWRIGHT_VERSION}-noble@sha256:"):
        raise ValueError("browser runner must use the pinned Playwright image digest")
    if not image_id.startswith("sha256:"):
        raise ValueError("browser runner image ID is not a digest")
    return {"image": image, "image_id": image_id, "playwright_version": PLAYWRIGHT_VERSION, "cached_before_measurement": cached}


def validate_events(events: list[dict]) -> dict:
    """Validate one page's same-clock, safe browser event sequence."""
    if not isinstance(events, list) or not events:
        raise ValueError("browser run has no events")
    types = [event.get("type") for event in events]
    positions: dict[str, int] = {}
    for event_type in EVENT_TYPES:
        if types.count(event_type) != 1:
            raise ValueError(f"browser run needs exactly one {event_type} event")
        positions[event_type] = types.index(event_type)
    if [positions[event_type] for event_type in EVENT_TYPES] != sorted(positions.values()):
        raise ValueError("browser events are not ordered POST to subscribe to terminal to detail to render")
    execution_ids = {event.get("execution_id") for event in events if event.get("execution_id")}
    if len(execution_ids) != 1 or not all(isinstance(event.get("t_ms"), (int, float)) and event["t_ms"] >= 0 for event in events):
        raise ValueError("browser events need one execution ID and nonnegative performance timestamps")
    execution_id = next(iter(execution_ids))
    if any(event.get("execution_id") != execution_id for event in events[positions["post"]:]):
        raise ValueError("browser transport events are not correlated to one execution")
    safe_keys = {"type", "t_ms", "execution_id", "status", "method", "path", "channel"}
    if any(set(event) - safe_keys for event in events):
        raise ValueError("browser event includes unsafe payload context")
    return {"execution_id": execution_id, "events": events, "browser_wall_ms": events[-1]["t_ms"] - events[0]["t_ms"]}


def decode_driver_output(text: str) -> list[dict]:
    """Read one JSON line from the browser runner without accepting log noise."""
    lines = [line for line in text.splitlines() if line.startswith("{")]
    if len(lines) != 1:
        raise ValueError("browser runner did not emit exactly one JSON result")
    result = json.loads(lines[0])
    if not isinstance(result, dict) or not isinstance(result.get("runs"), list):
        raise ValueError("browser runner result has no runs")
    return result["runs"]
