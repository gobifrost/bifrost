#!/usr/bin/env python3
"""Measure Bifrost runtime import and construction memory in fresh processes.

This diagnostic is intentionally stdlib-only at module import time. The parent
process launches a new Python interpreter for each requested stage so import
costs are not hidden by earlier stages in the same process.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_RESULT_PREFIX = "ELASTIC_RUNTIME_MEMORY_JSON="


@dataclass(frozen=True)
class Stage:
    name: str
    description: str
    handler: Callable[[argparse.Namespace], None]


def _read_int(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_proc_status(pid: int | None = None) -> dict[str, int | None]:
    pid = pid or os.getpid()
    values: dict[str, int | None] = {
        "rss_bytes": None,
        "vm_peak_bytes": None,
        "vm_size_bytes": None,
    }
    with suppress(OSError, IndexError, ValueError):
        with Path(f"/proc/{pid}/status").open(encoding="utf-8") as status:
            for line in status:
                key, _, value = line.partition(":")
                if key == "VmRSS":
                    values["rss_bytes"] = int(value.split()[0]) * 1024
                elif key == "VmPeak":
                    values["vm_peak_bytes"] = int(value.split()[0]) * 1024
                elif key == "VmSize":
                    values["vm_size_bytes"] = int(value.split()[0]) * 1024
    return values


def _read_smaps_rollup(pid: int | None = None) -> dict[str, int | None]:
    pid = pid or os.getpid()
    values: dict[str, int | None] = {
        "pss_bytes": None,
        "private_dirty_bytes": None,
        "shared_clean_bytes": None,
    }
    with suppress(OSError, IndexError, ValueError):
        with Path(f"/proc/{pid}/smaps_rollup").open(encoding="utf-8") as smaps:
            for line in smaps:
                key, _, value = line.partition(":")
                if key == "Pss":
                    values["pss_bytes"] = int(value.split()[0]) * 1024
                elif key == "Private_Dirty":
                    values["private_dirty_bytes"] = int(value.split()[0]) * 1024
                elif key == "Shared_Clean":
                    values["shared_clean_bytes"] = int(value.split()[0]) * 1024
    return values


def _self_cgroup_relpaths() -> dict[str, str]:
    relpaths: dict[str, str] = {}
    try:
        lines = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return relpaths
    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        _, controllers, relpath = parts
        if controllers == "":
            relpaths["unified"] = relpath
            continue
        for controller in controllers.split(","):
            relpaths[controller] = relpath
    return relpaths


def _cgroup_root() -> Path:
    override = os.environ.get("BIFROST_MEMORY_DIAGNOSTIC_CGROUP_ROOT")
    return Path(override) if override else Path("/sys/fs/cgroup")


def _cgroup_v2_dir(root: Path, relpaths: dict[str, str]) -> Path | None:
    relpath = relpaths.get("unified")
    if relpath is None:
        return None
    return root / relpath.lstrip("/")


def _read_cgroup_stat(path: Path) -> dict[str, int]:
    stat: dict[str, int] = {}
    with suppress(OSError, ValueError):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split()[:2]
                stat[key] = int(value)
    return stat


def read_cgroup_memory() -> dict[str, Any]:
    root = _cgroup_root()
    relpaths = _self_cgroup_relpaths()
    v2 = _cgroup_v2_dir(root, relpaths)
    if v2 is None:
        raise RuntimeError("cgroup_v2_unavailable")
    if not (v2 / "memory.current").exists():
        raise RuntimeError("cgroup_v2_memory_unavailable")

    current_bytes = _read_int(v2 / "memory.current")
    stat = _read_cgroup_stat(v2 / "memory.stat")
    return {
        "version": "v2",
        "path": str(v2),
        "current_bytes": current_bytes,
        "peak_bytes": _read_int(v2 / "memory.peak"),
        "limit_bytes": _read_int(v2 / "memory.max"),
        "stat": stat,
        "working_set_estimate_bytes": _working_set_estimate(current_bytes, stat),
    }


def _working_set_estimate(current_bytes: int | None, stat: dict[str, int]) -> int | None:
    if current_bytes is None:
        return None
    return max(0, current_bytes - stat.get("inactive_file", 0))


def collect_memory_sample() -> dict[str, Any]:
    return {
        "proc": {
            **_read_proc_status(),
            **_read_smaps_rollup(),
        },
        "cgroup": read_cgroup_memory(),
    }


def _stage_python(_args: argparse.Namespace) -> None:
    return None


def _stage_config(_args: argparse.Namespace) -> None:
    from src.config import get_settings

    get_settings()


def _stage_database(_args: argparse.Namespace) -> None:
    from sqlalchemy.orm import configure_mappers

    from src.core.database import get_session_factory

    get_session_factory()
    configure_mappers()


def _stage_rabbitmq(_args: argparse.Namespace) -> None:
    from src.jobs.rabbitmq import rabbitmq

    _ = rabbitmq


def _stage_workflow_consumer_import(_args: argparse.Namespace) -> None:
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer

    _ = WorkflowExecutionConsumer


def _stage_worker_app_import(_args: argparse.Namespace) -> None:
    import src.worker.app  # noqa: F401


def _stage_construct_worker_consumers(_args: argparse.Namespace) -> None:
    from src.jobs.consumers.agent_run import AgentRunConsumer
    from src.jobs.consumers.package_install import PackageInstallConsumer
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
    from src.jobs.summarize_worker import (
        SummarizeBackfillConsumer,
        SummarizeConsumer,
        TuneChatConsumer,
    )

    consumers = [
        WorkflowExecutionConsumer(),
        PackageInstallConsumer(),
        AgentRunConsumer(),
        SummarizeConsumer(),
        SummarizeBackfillConsumer(),
        TuneChatConsumer(),
    ]
    _ = [consumer.queue_name for consumer in consumers]


def _stage_scheduler_import(_args: argparse.Namespace) -> None:
    import src.jobs.schedulers.platform_jobs  # noqa: F401


def _stage_platform_registry(_args: argparse.Namespace) -> None:
    from src.jobs.platform.registry import get_platform_job_definition

    _ = get_platform_job_definition


def _stage_selected_platform_job(args: argparse.Namespace) -> None:
    from src.jobs.platform.registry import get_platform_job_definition

    definition = get_platform_job_definition(args.platform_job_type)
    if definition is None:
        raise SystemExit(2)
    _ = (
        definition.job_type,
        definition.payload_model,
        definition.handler,
        definition.policy,
    )


def _stage_application_sdk_update_module(_args: argparse.Namespace) -> None:
    from src.jobs.platform.application_sdk_update import (
        APPLICATION_SDK_UPDATE_DEFINITION,
    )

    definition = APPLICATION_SDK_UPDATE_DEFINITION
    _ = (
        definition.job_type,
        definition.payload_model,
        definition.handler,
        definition.policy,
    )


STAGES: tuple[Stage, ...] = (
    Stage("python", "Python interpreter after stdlib diagnostic startup", _stage_python),
    Stage("config", "Bifrost configuration imported and settings loaded", _stage_config),
    Stage("database", "Database layer imported and SQLAlchemy mappers configured", _stage_database),
    Stage("rabbitmq", "RabbitMQ transport module imported", _stage_rabbitmq),
    Stage("workflow-consumer-import", "Workflow execution consumer class imported", _stage_workflow_consumer_import),
    Stage("worker-app-import", "Full worker app module imported", _stage_worker_app_import),
    Stage("construct-worker-consumers", "All worker consumer instances constructed without starting IO", _stage_construct_worker_consumers),
    Stage("scheduler-import", "PlatformJob scheduler module imported", _stage_scheduler_import),
    Stage("platform-registry", "PlatformJob registry imported", _stage_platform_registry),
    Stage("selected-platform-job", "Selected PlatformJob definition resolved", _stage_selected_platform_job),
    Stage("application-sdk-update-module", "application.sdk_update handler module imported directly", _stage_application_sdk_update_module),
)


def _stage_by_name() -> dict[str, Stage]:
    return {stage.name: stage for stage in STAGES}


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value / 1024 / 1024:.1f} MiB"


def _run_child(args: argparse.Namespace) -> int:
    stage = _stage_by_name()[args.child_stage]
    started = time.perf_counter()
    try:
        stage.handler(args)
        ok = True
        error = None
    except (KeyboardInterrupt, SystemExit) as exc:
        # A diagnostic child must report a sanitized stage error for
        # interpreter-level interruptions too, never propagate them.
        ok = False
        error = type(exc).__name__
    except Exception as exc:
        ok = False
        error = type(exc).__name__
    elapsed_ms = (time.perf_counter() - started) * 1000
    result = {
        "stage": stage.name,
        "description": stage.description,
        "ok": ok,
        "error": error,
        "elapsed_ms": round(elapsed_ms, 3),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "memory": collect_memory_sample(),
    }
    print(_RESULT_PREFIX + json.dumps(result, sort_keys=True))
    return 0 if ok else 1


def _repo_pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH")
    parts: list[str] = []
    for candidate in _candidate_repo_roots():
        api = candidate / "api"
        if (api / "src").is_dir():
            parts.append(str(api))
            parts.append(str(candidate))
            break
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts) if parts else existing or ""


def _candidate_repo_roots() -> list[Path]:
    candidates: list[Path] = []
    for path in Path(__file__).resolve().parents:
        candidates.append(path)
    cwd = Path.cwd().resolve()
    candidates.append(cwd)
    candidates.extend(cwd.parents)
    app = Path("/app")
    if app.exists():
        candidates.append(app)
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def _run_stage(stage: Stage, args: argparse.Namespace) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = _repo_pythonpath()
    cmd = [
        sys.executable,
        "-m",
        "scripts.elastic_runtime_memory",
        "--_child-stage",
        stage.name,
        "--platform-job-type",
        args.platform_job_type,
    ]
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=args.stage_timeout,
    )
    result: dict[str, Any] | None = None
    for line in proc.stdout.splitlines():
        if line.startswith(_RESULT_PREFIX):
            result = json.loads(line[len(_RESULT_PREFIX) :])
    if result is None:
        result = {
            "stage": stage.name,
            "description": stage.description,
            "ok": False,
            "error": "child did not emit a diagnostic result",
            "elapsed_ms": None,
            "pid": None,
            "python": None,
            "memory": collect_memory_sample(),
        }
    result["returncode"] = proc.returncode
    return result


def _print_table(results: list[dict[str, Any]]) -> None:
    headers = [
        "stage",
        "ok",
        "rss",
        "pss",
        "cgroup_current",
        "cgroup_peak",
        "working_set",
        "elapsed_ms",
        "error",
    ]
    rows = []
    for result in results:
        memory = result["memory"]
        proc = memory["proc"]
        cgroup = memory["cgroup"]
        rows.append(
            [
                result["stage"],
                "yes" if result["ok"] else "no",
                _format_bytes(proc.get("rss_bytes")),
                _format_bytes(proc.get("pss_bytes")),
                _format_bytes(cgroup.get("current_bytes")),
                _format_bytes(cgroup.get("peak_bytes")),
                _format_bytes(cgroup.get("working_set_estimate_bytes")),
                "n/a" if result.get("elapsed_ms") is None else f"{result['elapsed_ms']:.1f}",
                _short_error(result.get("error")),
            ]
        )
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def _short_error(error: object) -> str:
    if not error:
        return ""
    text = str(error).replace("\n", " ")
    return text if len(text) <= 80 else text[:77] + "..."


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        action="append",
        choices=[stage.name for stage in STAGES] + ["all"],
        default=None,
        help="Stage to measure. Repeat for multiple stages. Defaults to all.",
    )
    parser.add_argument(
        "--platform-job-type",
        default="application.sdk_update",
        help="PlatformJob type for the selected-platform-job stage.",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format for the parent summary.",
    )
    parser.add_argument(
        "--stage-timeout",
        type=float,
        default=60.0,
        help="Seconds before one child stage is killed.",
    )
    parser.add_argument(
        "--list-stages",
        action="store_true",
        help="List available stages and exit.",
    )
    parser.add_argument("--_child-stage", dest="child_stage", choices=[stage.name for stage in STAGES], help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.child_stage:
        return _run_child(args)
    if args.list_stages:
        for stage in STAGES:
            print(f"{stage.name}\t{stage.description}")
        return 0

    selected_names = args.stage or ["all"]
    if "all" in selected_names:
        selected = list(STAGES)
    else:
        by_name = _stage_by_name()
        selected = [by_name[name] for name in selected_names]

    results = [_run_stage(stage, args) for stage in selected]
    if args.format == "json":
        print(json.dumps({"results": results}, indent=2, sort_keys=True))
    else:
        _print_table(results)
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
