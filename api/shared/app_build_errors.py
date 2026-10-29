"""Consistent, actionable memory failures for App builds and their supervisors."""

from __future__ import annotations

import re
from typing import Literal


APP_BUILD_JOB_TYPES = frozenset({
    "application.deploy",
    "application.publish",
    "application.sdk_update",
    "solution.deploy",
    "solution.git_sync",
})

_GUIDANCE = (
    "To retry, increase the build container's memory limit. For non-Git-connected "
    "Solutions, run `bifrost solution deploy` locally to build and deploy prebuilt "
    "output instead."
)


def app_build_memory_message(
    reason: Literal["oom", "pressure", "killed"] = "oom",
) -> str:
    explanation = {
        "oom": "The app build ran out of memory (OOM).",
        "pressure": (
            "The app build was stopped because its container was running out of "
            "memory, to prevent an out-of-memory (OOM) failure."
        ),
        "killed": (
            "The app build process was killed (SIGKILL/exit 137), which can "
            "indicate an out-of-memory (OOM) failure."
        ),
    }[reason]
    return f"{explanation} {_GUIDANCE}"


class AppBuildMemoryError(ValueError):
    """A build memory failure whose safe message can cross caller boundaries."""

    def __init__(self, reason: Literal["oom", "pressure", "killed"] = "oom") -> None:
        super().__init__(app_build_memory_message(reason))


def subprocess_memory_error(
    returncode: int | None,
    stderr: bytes | str | None,
    stdout: bytes | str | None = None,
) -> AppBuildMemoryError | None:
    """Recognize allocator diagnostics; an unexplained SIGKILL is only probable OOM."""
    if returncode is None or returncode == 0:
        return None
    # Allocator diagnostics appear at termination. Avoid copying an entire noisy
    # build log while the container is already short of memory.
    output = "\n".join(
        value[-16384:].decode(errors="replace") if isinstance(value, bytes)
        else (value or "")[-16384:]
        for value in (stderr, stdout)
    ).casefold()
    if any(marker in output for marker in (
        "javascript heap out of memory",
        "err_worker_out_of_memory",
        "runtime: out of memory",
        "fatal error: out of memory",
    )) or re.search(r"\benomem\b|memory allocation of \d+ bytes failed", output):
        return AppBuildMemoryError()
    if returncode in (-9, 137):
        return AppBuildMemoryError("killed")
    return None
