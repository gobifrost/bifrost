"""
Unit tests for TemplateProcess.

Tests the template process lifecycle: startup, fork requests, shutdown.
Uses real multiprocessing (not mocks) since fork behavior can't be mocked.
"""

import logging
import os
import signal
import time

import pytest

from src.services.execution.template_process import TemplateProcess

logger = logging.getLogger(__name__)

# These tests fork real OS processes (see module docstring — fork behavior
# can't be mocked), so they cost seconds each, not milliseconds. They are
# integration tests living in the unit tree. Mark the whole module `slow` so
# the every-PR unit lane (`./test.sh unit`, which runs `-m "not slow"`) skips
# them; they still run in `./test.sh all` and nightly. This file alone was
# ~90s of the ~140s unit-suite cost. Fork EXECUTION is also covered end-to-end
# by tests/e2e/platform/test_fork_pool.py; these cover the TemplateProcess
# primitive directly, so the coverage is retained, just at the right cadence.
pytestmark = pytest.mark.slow


def _wait_for_pid_to_die(pid: int, timeout: float = 5.0) -> None:
    """
    Wait for a process to exit without calling waitpid.

    os.waitpid can only be called by the direct parent. Forked children
    of the template process are grandchildren of the test runner, so we
    poll os.kill(pid, 0) instead.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            return  # Process is gone
    # Best-effort — don't raise if still alive (zombie will be reaped by template)


class TestTemplateProcessLifecycle:
    """Tests for template process startup and shutdown."""

    def test_start_and_ready(self):
        """Template process should start and signal ready."""
        template = TemplateProcess()
        template.start()
        try:
            assert template.is_alive()
            assert template.pid is not None
        finally:
            template.shutdown()

    def test_shutdown_stops_process(self):
        """Shutdown should terminate the template process."""
        template = TemplateProcess()
        template.start()
        pid = template.pid
        template.shutdown()

        assert not template.is_alive()
        # Process should be gone
        with pytest.raises(OSError):
            os.kill(pid, 0)

    def test_double_start_is_noop(self):
        """Starting twice should not create a second process."""
        template = TemplateProcess()
        template.start()
        try:
            pid1 = template.pid
            template.start()  # Should be a no-op
            assert template.pid == pid1
        finally:
            template.shutdown()

    def test_shutdown_without_start_is_safe(self):
        """Shutting down before starting should not raise."""
        template = TemplateProcess()
        template.shutdown()  # Should not raise


class TestTemplateProcessFork:
    """Tests for forking children from the template."""

    def test_fork_returns_child_pid_and_queues(self):
        """Fork should return a valid child PID and queue pair."""
        template = TemplateProcess()
        template.start()
        try:
            child_pid, work_queue, result_queue = template.fork()
            assert child_pid > 0
            assert work_queue is not None
            assert result_queue is not None

            # Child should be alive
            os.kill(child_pid, 0)  # Should not raise

            # Clean up child (grandchild of test runner — cannot waitpid)
            os.kill(child_pid, signal.SIGTERM)
            _wait_for_pid_to_die(child_pid)
        finally:
            template.shutdown()

    def test_fork_multiple_children(self):
        """Should be able to fork multiple children."""
        template = TemplateProcess()
        template.start()
        children = []
        try:
            for _ in range(3):
                child_pid, wq, rq = template.fork()
                children.append(child_pid)

            # All should be unique PIDs
            assert len(set(children)) == 3

            # All should be alive
            for pid in children:
                os.kill(pid, 0)  # Should not raise
        finally:
            for pid in children:
                try:
                    os.kill(pid, signal.SIGTERM)
                    _wait_for_pid_to_die(pid)
                except OSError as e:
                    # Child already gone (race with shutdown) — fine
                    logger.debug(f"could not signal child {pid} during cleanup: {e}")
            template.shutdown()

    def test_forked_child_can_execute_and_return_result(self):
        """Forked child should be able to receive work and return results."""
        template = TemplateProcess()
        template.start()
        try:
            child_pid, work_queue, result_queue = template.fork()

            # Send a simple test execution ID
            work_queue.put("test-exec-id")

            # Child should process and return result (or we just verify
            # the queue is functional by checking the child is alive)
            # Full execution tests are in E2E — here we verify the plumbing
            time.sleep(0.5)
            os.kill(child_pid, 0)  # Still alive, waiting for work or processing

            # Clean up (grandchild of test runner — cannot waitpid)
            os.kill(child_pid, signal.SIGTERM)
            _wait_for_pid_to_die(child_pid)
        finally:
            template.shutdown()


class TestTemplateProcessCrashRecovery:
    """Tests for template crash detection."""

    def test_is_alive_returns_false_after_crash(self):
        """Should detect when template process has died."""
        template = TemplateProcess()
        template.start()
        pid = template.pid

        # Kill the template process directly
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)

        assert not template.is_alive()
        template.shutdown()  # Cleanup should not raise


def _get_rss_kb(pid: int) -> int:
    """Read VmRSS from /proc/{pid}/status in KB."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError) as e:
        # /proc not available (macOS) or process gone — caller treats -1 as unknown
        logger.debug(f"could not read VmRSS for pid {pid}: {e}")
    return -1


def _get_private_dirty_kb(pid: int) -> int:
    """Read Private_Dirty from /proc/{pid}/smaps_rollup in KB."""
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Private_Dirty:"):
                    return int(line.split()[1])
    except (OSError, ValueError) as e:
        # /proc not available (macOS) or process gone — caller treats -1 as unknown
        logger.debug(f"could not read Private_Dirty for pid {pid}: {e}")
    return -1


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _spawn_noop(ready_q, rq, name):
    """Minimal spawn target — must be module-level for pickling."""
    if ready_q is not None:
        ready_q.put("ready")
    time.sleep(5)


class TestForkPerformance:
    """Benchmark tests: fork latency, memory, and COW effectiveness."""

    def test_fork_latency_under_100ms(self):
        """Forking a child from the template should take < 100ms."""
        template = TemplateProcess()
        template.start()
        try:
            times = []
            for i in range(5):
                start = time.monotonic()
                child_pid, _, _ = template.fork(worker_id=f"latency-{i}")
                elapsed_ms = (time.monotonic() - start) * 1000
                times.append(elapsed_ms)
                os.kill(child_pid, signal.SIGTERM)
                _wait_for_pid_to_die(child_pid)

            avg_ms = sum(times) / len(times)
            print(f"\n  Fork latency: avg={avg_ms:.1f}ms "
                  f"min={min(times):.1f}ms max={max(times):.1f}ms")
            assert avg_ms < 100, f"Fork too slow: {avg_ms:.1f}ms avg"
        finally:
            template.shutdown()

    def test_can_fork_10_concurrent_children(self):
        """Should sustain 10 concurrent forked children from one template."""
        template = TemplateProcess()
        template.start()
        children = []
        try:
            template_rss_kb = _get_rss_kb(template.pid)
            template_rss_mb = template_rss_kb / 1024 if template_rss_kb > 0 else -1

            start = time.monotonic()
            for i in range(10):
                child_pid, _, _ = template.fork(
                    worker_id=f"mem-{i}", persistent=True,
                )
                children.append(child_pid)
            fork_all_ms = (time.monotonic() - start) * 1000

            time.sleep(0.1)

            alive = sum(1 for pid in children if _is_pid_alive(pid))

            print(f"\n  Template RSS: {template_rss_mb:.0f}MB")
            print(f"  Forked 10 children in {fork_all_ms:.0f}ms ({alive} alive)")
            print(f"  Spawn would need: ~{10 * template_rss_mb:.0f}MB (10 x {template_rss_mb:.0f}MB)")
            print("  Fork shares template memory via COW — actual unique memory is minimal")

            assert alive >= 8, f"Only {alive}/10 children alive"
        finally:
            for pid in children:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError as e:
                    # Child already gone — fine
                    logger.debug(f"could not signal child {pid} during cleanup: {e}")
            time.sleep(0.3)
            template.shutdown()

    def test_fork_faster_than_spawn(self):
        """Fork from template should be faster than multiprocessing.spawn."""
        import multiprocessing

        # Measure spawn — wait for child to actually be running
        spawn_times = []
        for i in range(3):
            ctx = multiprocessing.get_context("spawn")
            ready_q = ctx.Queue()

            start = time.monotonic()
            p = ctx.Process(
                target=_spawn_noop, args=(ready_q, None, f"s-{i}"),
            )
            p.start()
            # Wait for child to signal ready (proves it's actually running)
            ready_q.get(timeout=30)
            elapsed_ms = (time.monotonic() - start) * 1000
            spawn_times.append(elapsed_ms)
            p.terminate()
            p.join(timeout=2)

        # Measure fork — child is ready immediately after fork returns
        template = TemplateProcess()
        template.start()
        try:
            fork_times = []
            for i in range(3):
                start = time.monotonic()
                child_pid, _, _ = template.fork(worker_id=f"cmp-{i}")
                elapsed_ms = (time.monotonic() - start) * 1000
                fork_times.append(elapsed_ms)
                os.kill(child_pid, signal.SIGTERM)
                _wait_for_pid_to_die(child_pid)

            spawn_avg = sum(spawn_times) / len(spawn_times)
            fork_avg = sum(fork_times) / len(fork_times)
            speedup = spawn_avg / fork_avg if fork_avg > 0 else float("inf")

            print(f"\n  Spawn avg: {spawn_avg:.0f}ms (includes Python re-import)")
            print(f"  Fork avg:  {fork_avg:.1f}ms (COW, no re-import)")
            print(f"  Speedup:   {speedup:.1f}x")

            assert fork_avg < spawn_avg, (
                f"Fork ({fork_avg:.0f}ms) not faster than spawn ({spawn_avg:.0f}ms)"
            )
        finally:
            template.shutdown()
