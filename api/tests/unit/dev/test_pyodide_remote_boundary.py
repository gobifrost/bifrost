"""Current remote admission constraints, not claims of Pyodide compatibility.

Keep these explicit until a reviewed provider boundary replaces the Linux
requirements. Inventing a PID or reporting unknown memory as zero is unsafe.
"""
import pytest

from src.worker.remote.remote_pool import ThinRemoteProcessPool


@pytest.mark.parametrize("pid", [None, False, 0, -1, "isolate-123"])
def test_current_remote_ack_rejects_non_process_identity(pid):
    with pytest.raises(RuntimeError, match="missing a valid PID"):
        ThinRemoteProcessPool._remote_pid_from_ack({"accepted": True, "pid": pid})


def test_current_remote_capacity_rejects_unavailable_provider_memory():
    class UnmeasuredTransport:
        hostname = "pyodide-boundary-test"

        def memory(self):
            return (-1, -1)

    pool = ThinRemoteProcessPool(UnmeasuredTransport())
    with pytest.raises(MemoryError, match="cgroup memory is unavailable"):
        pool._ensure_remote_capacity()
