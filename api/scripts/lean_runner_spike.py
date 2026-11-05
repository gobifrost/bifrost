#!/usr/bin/env python3
"""Bounded import/construction probes for a lean platform-job parent.

This is a diagnostic spike, not a runner.  It does not claim work, heartbeat a
lease, fence an attempt, own durable state, or execute normal PlatformJob
handlers.  The optional ``platform-runner`` child exists solely for one
controlled live-runner integration fixture; its lease identifiers travel over a
private inherited pipe and are never included in diagnostic output.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import select
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Mapping


_RESULT_PREFIX = "LEAN_RUNNER_SPIKE_JSON="
_FORBIDDEN_PARENT_PREFIXES = (
    "sqlalchemy",
    "fastapi",
    "pydantic_ai",
    "openai",
    "anthropic",
    "google.genai",
)
_PROBE_STAGES = frozenset(
    {"registry", "selected", "agent-provider", "heavy-payload", "fail", "timeout", "platform-runner"}
)
_CANONICAL_REFERENCE_FINGERPRINTS: set[str] = set()


class ChildReadinessError(RuntimeError):
    """A child did not reach the explicit post-import ready point."""


class ChildActiveProtocolError(RuntimeError):
    """A released child did not acknowledge the start of active work."""


class ChildResultError(RuntimeError):
    """A child completed without its compact diagnostic result."""


def _forbidden_parent_modules(modules: set[str] | None = None) -> list[str]:
    """Return heavy modules that a thin controller must never import."""
    modules = modules if modules is not None else set(sys.modules)
    return sorted(
        name
        for name in modules
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in _FORBIDDEN_PARENT_PREFIXES)
    )


def _read_int(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return None if raw == "max" else int(raw)
    except (OSError, ValueError):
        return None


def _read_proc_memory(pid: int) -> dict[str, int | None]:
    values: dict[str, int | None] = {"rss_bytes": None, "pss_bytes": None}
    with suppress(OSError, ValueError, IndexError):
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            if key == "VmRSS":
                values["rss_bytes"] = int(value.split()[0]) * 1024
    with suppress(OSError, ValueError, IndexError):
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            if key == "Pss":
                values["pss_bytes"] = int(value.split()[0]) * 1024
    return values


def _self_cgroup_dir() -> Path | None:
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            _, controllers, relpath = line.split(":", 2)
            if not controllers:
                return Path("/sys/fs/cgroup") / relpath.lstrip("/")
    except OSError:
        return None
    return None


def _read_cgroup_memory() -> dict[str, int | None]:
    cgroup = _self_cgroup_dir()
    if cgroup is None:
        return {"current_bytes": None, "inactive_file_bytes": None, "working_set_estimate_bytes": None}
    current = _read_int(cgroup / "memory.current")
    inactive: int | None = None
    with suppress(OSError, ValueError):
        for line in (cgroup / "memory.stat").read_text(encoding="utf-8").splitlines():
            key, value = line.split(maxsplit=1)
            if key == "inactive_file":
                inactive = int(value)
                break
    return {
        "current_bytes": current,
        "inactive_file_bytes": inactive,
        "working_set_estimate_bytes": None if current is None else max(0, current - (inactive or 0)),
    }


def collect_memory_sample(pid: int | None = None) -> dict[str, Any]:
    """Capture process RSS/PSS and cgroup-v2 working-set approximation."""
    return {"proc": _read_proc_memory(pid or os.getpid()), "cgroup": _read_cgroup_memory()}


def sample_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, int | None]:
    """Return the comparable process/cgroup deltas, preserving unavailable data."""
    pairs = {
        "rss_bytes": (before["proc"].get("rss_bytes"), after["proc"].get("rss_bytes")),
        "pss_bytes": (before["proc"].get("pss_bytes"), after["proc"].get("pss_bytes")),
        "working_set_estimate_bytes": (
            before["cgroup"].get("working_set_estimate_bytes"),
            after["cgroup"].get("working_set_estimate_bytes"),
        ),
    }
    return {name: None if left is None or right is None else right - left for name, (left, right) in pairs.items()}


def _repo_pythonpath() -> str:
    api_root = Path(__file__).resolve().parents[1]
    existing = os.environ.get("PYTHONPATH")
    return os.pathsep.join(part for part in (str(api_root), existing) if part)


def _read_line(fd: int, timeout_seconds: float) -> bytes | None:
    readable, _, _ = select.select([fd], [], [], timeout_seconds)
    return os.read(fd, 4096) if readable else None


def _write_private_metadata(fd: int, metadata: Mapping[str, str] | None) -> None:
    if metadata:
        os.write(fd, json.dumps(dict(metadata)).encode("utf-8"))
    os.close(fd)


def _kill_process_group(process: subprocess.Popen[bytes]) -> bool:
    """Kill a group even when its leader has already exited."""
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
    return process.poll() is not None


def _result_from_child_stdout(stdout: bytes) -> dict[str, Any] | None:
    line = next((item for item in stdout.splitlines() if item.startswith(_RESULT_PREFIX.encode())), None)
    if line is None:
        return None
    try:
        value = json.loads(line[len(_RESULT_PREFIX) :])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _reference_fingerprint(metadata: Mapping[str, str]) -> str:
    return json.dumps(dict(metadata), sort_keys=True, separators=(",", ":"))


def launch_probe_child(
    stage: str,
    *,
    metadata: Mapping[str, str] | None = None,
    timeout_seconds: float = 30,
    hold_seconds: float = 0,
    sample_interval_seconds: float = 0.05,
) -> dict[str, Any]:
    """Launch one fixed diagnostic child and sample it after an explicit ready signal.

    ``metadata`` is accepted only for the canonical selected-definition probe or
    the private ``platform-runner`` bridge.  It never appears in results.
    """
    if stage not in _PROBE_STAGES:
        raise ValueError("unknown_probe_stage")
    if timeout_seconds <= 0 or not 0 <= hold_seconds <= 5 or not 0.01 <= sample_interval_seconds <= 1:
        raise ValueError("invalid_probe_bounds")
    if stage in {"registry", "selected"}:
        if not metadata:
            raise ValueError("canonical_probe_requires_metadata")
        if not _CANONICAL_REFERENCE_FINGERPRINTS:
            get_canonical_job_inventory(timeout_seconds=timeout_seconds)
        if _reference_fingerprint(metadata) not in _CANONICAL_REFERENCE_FINGERPRINTS:
            raise ValueError("noncanonical_job_reference")
    if stage == "platform-runner" and set((metadata or {})) != {"job_id", "lease_token"}:
        raise ValueError("platform_runner_requires_private_lease_metadata")

    metadata_read, metadata_write = os.pipe()
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    active_read, active_write = os.pipe()
    parent_baseline = collect_memory_sample()
    env = os.environ.copy()
    env["PYTHONPATH"] = _repo_pythonpath()
    command = [
        sys.executable,
        "-m",
        "scripts.lean_runner_spike",
        "--_child-stage",
        stage,
        "--_metadata-fd",
        str(metadata_read),
        "--_ready-fd",
        str(ready_write),
        "--_release-fd",
        str(release_read),
        "--_active-fd",
        str(active_write),
    ]
    process: subprocess.Popen[bytes] | None = None
    samples: list[dict[str, Any]] = [{"label": "parent-baseline", "pid": os.getpid(), "memory": parent_baseline}]
    result: dict[str, Any] = {
        "stage": stage,
        "ok": False,
        "error": None,
        "returncode": None,
        "samples": samples,
        "process_group_reaped": False,
        "phase_times_ms": {},
    }
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            pass_fds=(metadata_read, ready_write, release_read, active_write),
            start_new_session=True,
        )
        os.close(metadata_read)
        metadata_read = -1
        os.close(ready_write)
        ready_write = -1
        os.close(release_read)
        release_read = -1
        os.close(active_write)
        active_write = -1
        _write_private_metadata(metadata_write, metadata)
        metadata_write = -1
        ready = _read_line(ready_read, timeout_seconds)
        if ready != b"ready\n":
            stdout, _ = process.communicate(timeout=1)
            child_result = _result_from_child_stdout(stdout)
            result["error"] = child_result.get("error") if child_result else ChildReadinessError.__name__
            result["returncode"] = process.returncode
            result["process_group_reaped"] = _kill_process_group(process)
            return result
        ready_at = time.perf_counter()
        result["phase_times_ms"]["spawn_to_ready"] = round((ready_at - started) * 1000, 3)
        samples.append({"label": "child-ready", "pid": process.pid, "memory": collect_memory_sample(process.pid)})
        hold_deadline = time.monotonic() + hold_seconds
        while time.monotonic() < hold_deadline:
            time.sleep(min(sample_interval_seconds, hold_deadline - time.monotonic()))
            samples.append({"label": "child-held", "pid": process.pid, "memory": collect_memory_sample(process.pid)})
        os.write(release_write, b"release\n")
        os.close(release_write)
        release_write = -1
        active = _read_line(active_read, timeout_seconds)
        if active != b"active\n":
            stdout, _ = process.communicate(timeout=1)
            child_result = _result_from_child_stdout(stdout)
            result["error"] = child_result.get("error") if child_result else ChildActiveProtocolError.__name__
            result["returncode"] = process.returncode
            result["process_group_reaped"] = _kill_process_group(process)
            return result
        active_at = time.perf_counter()
        result["phase_times_ms"]["ready_hold"] = round((active_at - ready_at) * 1000, 3)
        samples.append({"label": "child-active", "pid": process.pid, "memory": collect_memory_sample(process.pid)})
        # The fault probe times out execution, after the same readiness
        # handshake as every other stage. A slow interpreter start must not
        # masquerade as successful coverage of a running-child timeout.
        execution_timeout = 0.2 if stage == "timeout" else timeout_seconds
        deadline = time.monotonic() + execution_timeout
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(sample_interval_seconds)
            if process.poll() is None:
                samples.append({"label": "child-executing", "pid": process.pid, "memory": collect_memory_sample(process.pid)})
        if process.poll() is None:
            raise subprocess.TimeoutExpired(command, execution_timeout)
        stdout, _ = process.communicate(timeout=1)
        result["phase_times_ms"]["active_to_exit"] = round((time.perf_counter() - active_at) * 1000, 3)
        child_result = _result_from_child_stdout(stdout)
        if child_result is None:
            raise ChildResultError
        result.update({key: child_result.get(key) for key in ("ok", "error", "comparison", "runner_returncode")})
        result["returncode"] = process.returncode
        result["process_group_reaped"] = _kill_process_group(process)
    except subprocess.TimeoutExpired:
        result["error"] = "TimeoutExpired"
        if process is not None:
            result["process_group_reaped"] = _kill_process_group(process)
    except Exception as exc:
        result["error"] = type(exc).__name__
        if process is not None:
            result["process_group_reaped"] = _kill_process_group(process)
    finally:
        for fd in (metadata_read, metadata_write, ready_read, ready_write, release_read, release_write, active_read, active_write):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
        samples.append({"label": "parent-post-child", "pid": os.getpid(), "memory": collect_memory_sample()})
        result["phase_times_ms"]["total"] = round((time.perf_counter() - started) * 1000, 3)
        if process is not None and result["returncode"] is None:
            result["returncode"] = process.poll()
        result["parent_delta"] = sample_delta(parent_baseline, samples[-1]["memory"])
    return result


def _definition_descriptor(definition: Any) -> dict[str, str]:
    module = importlib.import_module(definition.handler.__module__)
    names = [name for name, candidate in vars(module).items() if candidate is definition]
    if len(names) != 1:
        raise RuntimeError("CanonicalDefinitionReferenceError")
    return {
        "job_type": definition.job_type,
        "module": module.__name__,
        "definition_name": names[0],
        "payload_model": f"{definition.payload_model.__module__}.{definition.payload_model.__qualname__}",
        "handler": f"{definition.handler.__module__}.{definition.handler.__qualname__}",
    }


def _canonical_inventory_in_child() -> list[dict[str, str]]:
    from src.jobs.platform.registry import list_platform_job_definitions

    return sorted((_definition_descriptor(item) for item in list_platform_job_definitions()), key=lambda item: item["job_type"])


def get_canonical_job_inventory(*, timeout_seconds: float = 30) -> list[dict[str, str]]:
    """Query registry metadata in a short-lived helper, never the thin parent."""
    command = [sys.executable, "-m", "scripts.lean_runner_spike", "--_helper", "inventory"]
    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _repo_pythonpath()},
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("CanonicalInventoryError")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CanonicalInventoryError") from exc
    expected_keys = {"job_type", "module", "definition_name", "payload_model", "handler"}
    if not isinstance(value, list) or not all(
        isinstance(item, dict)
        and set(item) == expected_keys
        and all(isinstance(field, str) for field in item.values())
        for item in value
    ):
        raise RuntimeError("CanonicalInventoryError")
    _CANONICAL_REFERENCE_FINGERPRINTS.update(_reference_fingerprint(item) for item in value)
    return value


def _child_ready_then_hold(ready_fd: int, release_fd: int) -> None:
    os.write(ready_fd, b"ready\n")
    os.close(ready_fd)
    if os.read(release_fd, 16) != b"release\n":
        raise RuntimeError("ChildReleaseProtocolError")
    os.close(release_fd)


def _read_child_metadata(metadata_fd: int) -> dict[str, str]:
    try:
        raw = os.read(metadata_fd, 4096)
    finally:
        os.close(metadata_fd)
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise RuntimeError("ChildMetadataError")
    return value


def _run_child_stage(stage: str, metadata: Mapping[str, str]) -> dict[str, Any] | None:
    if stage == "registry":
        from src.jobs.platform.registry import get_platform_job_definition

        definition = get_platform_job_definition(metadata["job_type"])
        if definition is None:
            raise RuntimeError("UnknownCanonicalJobType")
        return {"comparison": {"job_type": definition.job_type, "matches_canonical_helper": _definition_descriptor(definition) == dict(metadata)}}
    if stage == "selected":
        direct = getattr(importlib.import_module(metadata["module"]), metadata["definition_name"])
        return {"comparison": {"job_type": direct.job_type, "matches_canonical_helper": _definition_descriptor(direct) == dict(metadata)}}
    if stage == "agent-provider":
        from src.services.agent_runtime.model_factory import create_agent_model
        from src.services.execution.run_summarizer import summarize_run
        from src.services.llm.base import LLMConfig

        _ = summarize_run
        return {
            "hold": (
                create_agent_model(
                    LLMConfig(
                        provider="openai",
                        model="gpt-4o-mini",
                        api_key="synthetic-key",
                    )
                ),
                create_agent_model(
                    LLMConfig(
                        provider="anthropic",
                        model="claude-3-5-haiku-latest",
                        api_key="synthetic-key",
                    )
                ),
            )
        }
    if stage == "heavy-payload":
        return {"hold": bytearray(32 * 1024 * 1024)}
    if stage == "fail":
        return {"raise_after_hold": "RuntimeError"}
    if stage == "timeout":
        return {"timeout_after_hold": True}
    if stage == "platform-runner":
        if set(metadata) != {"job_id", "lease_token"}:
            raise RuntimeError("ChildMetadataError")
        from src.jobs.platform.runner import _main
        return {"platform_runner": _main}
    raise RuntimeError("UnknownChildStage")


def _child_main(args: argparse.Namespace) -> int:
    metadata = _read_child_metadata(args._metadata_fd)
    started = time.perf_counter()
    try:
        deferred = _run_child_stage(args._child_stage, metadata)
        _child_ready_then_hold(args._ready_fd, args._release_fd)
        os.write(args._active_fd, b"active\n")
        os.close(args._active_fd)
        if deferred and deferred.get("raise_after_hold"):
            raise RuntimeError("synthetic failure")
        if deferred and deferred.get("timeout_after_hold"):
            while True:
                signal.pause()
        if args._child_stage == "platform-runner":
            import asyncio

            awaitable = deferred["platform_runner"](metadata["job_id"], metadata["lease_token"])
            runner_returncode = asyncio.run(awaitable)
            result: dict[str, Any] = {
                "ok": runner_returncode == 0,
                "error": None if runner_returncode == 0 else "PlatformRunnerFailed",
                "runner_returncode": runner_returncode,
            }
        else:
            result = {"ok": True, "error": None}
        if deferred and "comparison" in deferred:
            result["comparison"] = deferred["comparison"]
    except Exception as exc:
        with suppress(OSError):
            os.write(args._ready_fd, b"error\n")
        with suppress(OSError):
            os.write(args._active_fd, b"error\n")
        result = {"ok": False, "error": type(exc).__name__}
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    print(_RESULT_PREFIX + json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def run_cycle(
    *,
    template_factory: Callable[[], Any],
    job_filters: list[str],
    mode: str,
    timeout_seconds: float,
    hold_seconds: float,
    sample_interval_seconds: float = 0.05,
) -> dict[str, Any]:
    """Run construction-only probes while one genuine template remains warm."""
    template = template_factory()
    parent_baseline = collect_memory_sample()
    result: dict[str, Any] = {"probes": [], "template": {"started": False, "recovered": False}}
    try:
        template.start()
        result["template"] = {"started": True, "pid": template.pid, "alive_after_start": template.is_alive(), "memory": collect_memory_sample(template.pid)}
        inventory = get_canonical_job_inventory(timeout_seconds=timeout_seconds)
        selected = [item for item in inventory if not job_filters or item["job_type"] in job_filters]
        if job_filters and len(selected) != len(set(job_filters)):
            raise ValueError("unknown_job_type_filter")
        for item in selected:
            if mode in {"registry", "both"}:
                result["probes"].append(launch_probe_child("registry", metadata=item, timeout_seconds=timeout_seconds, hold_seconds=hold_seconds, sample_interval_seconds=sample_interval_seconds))
            if mode in {"selected", "both"}:
                result["probes"].append(launch_probe_child("selected", metadata=item, timeout_seconds=timeout_seconds, hold_seconds=hold_seconds, sample_interval_seconds=sample_interval_seconds))
        for stage in ("agent-provider", "heavy-payload", "fail", "timeout"):
            result["probes"].append(launch_probe_child(stage, timeout_seconds=timeout_seconds, hold_seconds=0 if stage in {"fail", "timeout"} else hold_seconds, sample_interval_seconds=sample_interval_seconds))
        result["parent_modules"] = {"forbidden": _forbidden_parent_modules()}
        result["parent_samples"] = {"baseline": parent_baseline, "active": collect_memory_sample()}
        result["template"]["alive_after_probes"] = template.is_alive()
    finally:
        template.shutdown()
        result["template"]["recovered"] = not template.is_alive()
        result.setdefault("parent_samples", {})["post_template"] = collect_memory_sample()
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--job-type", action="append", default=[])
    parser.add_argument("--mode", choices=("registry", "selected", "both"), default="both")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--hold-seconds", type=float, default=0)
    parser.add_argument("--sample-interval-seconds", type=float, default=0.05)
    parser.add_argument("--_helper", choices=("inventory",), help=argparse.SUPPRESS)
    parser.add_argument("--_child-stage", choices=sorted(_PROBE_STAGES), help=argparse.SUPPRESS)
    parser.add_argument("--_metadata-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--_ready-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--_release-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--_active-fd", type=int, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args._helper == "inventory":
        print(json.dumps(_canonical_inventory_in_child(), sort_keys=True))
        return 0
    if args._child_stage:
        if None in (args._metadata_fd, args._ready_fd, args._release_fd, args._active_fd):
            return 2
        return _child_main(args)
    if not 1 <= args.cycles <= 5:
        raise SystemExit("--cycles must be between 1 and 5")
    if args.timeout_seconds <= 0 or not 0 <= args.hold_seconds <= 5 or not 0.01 <= args.sample_interval_seconds <= 1:
        raise SystemExit("invalid bounded sampling arguments")
    from src.services.execution.template_process import TemplateProcess

    report = {
        "kind": "IMPORT/CONSTRUCTION probes only; not real handler execution or durable ownership tests",
        "cycles": [
            run_cycle(template_factory=TemplateProcess, job_filters=args.job_type, mode=args.mode, timeout_seconds=args.timeout_seconds, hold_seconds=args.hold_seconds, sample_interval_seconds=args.sample_interval_seconds)
            for _ in range(args.cycles)
        ],
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
