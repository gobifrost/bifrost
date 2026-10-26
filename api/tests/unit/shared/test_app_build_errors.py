"""Build diagnostics distinguish allocator failures from ambiguous process kills."""

import pytest

from shared.app_build_errors import app_build_memory_message, subprocess_memory_error


@pytest.mark.parametrize("diagnostic", [
    b"FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory",
    "Error [ERR_WORKER_OUT_OF_MEMORY]: Worker terminated due to memory limit",
    b"npm error code ENOMEM",
    b"runtime: out of memory",
    b"memory allocation of 33554432 bytes failed",
])
def test_allocator_failure_explains_oom_and_supported_recovery(diagnostic):
    error = subprocess_memory_error(134, diagnostic)
    assert str(error) == app_build_memory_message()
    assert "OOM" in str(error)
    assert "`bifrost solution deploy` locally" in str(error)
    assert "prebuilt output" in str(error)
    assert "increase the build container's memory limit" in str(error)


@pytest.mark.parametrize("returncode", [-9, 137])
def test_sigkill_is_reported_as_possible_oom(returncode):
    assert str(subprocess_memory_error(returncode, b"Killed")) == app_build_memory_message("killed")


def test_allocator_diagnostic_on_stdout_is_preserved():
    assert str(subprocess_memory_error(1, b"", "JavaScript heap out of memory")) == app_build_memory_message()


@pytest.mark.parametrize(("returncode", "output"), [
    (0, b"JavaScript heap out of memory"),
    (None, b"JavaScript heap out of memory"),
    (1, b"Could not resolve ./out-of-memory.tsx"),
    (134, b"Assertion failed"),
])
def test_non_memory_failures_and_successes_are_not_relabelled(returncode, output):
    assert subprocess_memory_error(returncode, output) is None
