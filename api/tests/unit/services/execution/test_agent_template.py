"""The agent template profile: warm agent runtime, one forked child per run.

Real template processes (fork behavior can't be mocked). The forked child's
``run_agent_message`` is replaced by a stub through a ``sitecustomize`` module
on the template's ``PYTHONPATH``, so these tests exercise the fork protocol
without a database or a model.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from src.services.execution.template_process import TemplateProcess
from src.worker.remote.identity import LauncherIdentity
from src.worker.remote.launcher import Launcher, proc_stat

pytestmark = pytest.mark.slow

_STUB = textwrap.dedent(
    """
    import asyncio

    import src.jobs.agent_run_child as agent_run_child


    async def run_agent_message(message):
        if "raise" in message:
            raise LookupError(message["raise"])
        if "exit" in message:
            raise SystemExit(message["exit"])
        await asyncio.sleep(message.get("sleep", 0))


    agent_run_child.run_agent_message = run_agent_message
    """
)


@pytest.fixture
def agent_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "sitecustomize.py").write_text(_STUB)
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(filter(None, [str(tmp_path), os.environ.get("PYTHONPATH")]))
    )
    template = TemplateProcess(profile="agent")
    template.start()
    try:
        yield template
    finally:
        template.shutdown()


def _wait_for_exit(template: TemplateProcess, pid: int, timeout: float = 10.0) -> int:
    """Exit status of a template child once the template has reaped it."""
    statuses: dict[int, int] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        statuses.update(template.collect_child_exit_statuses())
        if pid in statuses:
            return statuses[pid]
        time.sleep(0.05)
    raise AssertionError(f"agent child {pid} did not exit")


def test_agent_template_is_ready_with_a_frozen_heap(agent_template: TemplateProcess) -> None:
    assert agent_template.is_alive()
    assert agent_template.profile == "agent"
    assert agent_template.frozen_objects is not None and agent_template.frozen_objects > 0


def test_agent_template_preloads_shared_state_without_loading_the_api_app() -> None:
    probe = textwrap.dedent('''
        import json
        import sys
        from src.services.execution.template_process import _template_main

        class StartupPipe:
            def send(self, message):
                assert message["status"] == "ready", message
            def poll(self, timeout):
                return True
            def recv(self):
                return {"action": "shutdown"}

        _template_main(StartupPipe(), "agent")
        from genai_prices.data_snapshot import _bundled_snapshot
        from sqlalchemy import inspect
        from src.core import database
        from src.models.orm.agent_runs import AgentRun
        from src.services import model_catalog
        print(json.dumps({
            "main_loaded": "src.main" in sys.modules,
            "mappers_configured": inspect(AgentRun).configured,
            "catalog_parsed": model_catalog._cache.catalog is not None,
            "prices_parsed": _bundled_snapshot.cache_info().currsize == 1,
            "engine_left_open": database._engine is not None,
        }))
    ''')
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "main_loaded": False,
        "mappers_configured": True,
        "catalog_parsed": True,
        "prices_parsed": True,
        "engine_left_open": False,
    }


def test_agent_template_forks_without_threads_or_sockets(agent_template: TemplateProcess) -> None:
    pid = agent_template.pid
    assert os.listdir(f"/proc/{pid}/task") == [str(pid)]
    descriptors = []
    for fd in os.listdir(f"/proc/{pid}/fd"):
        try:
            descriptors.append(os.readlink(f"/proc/{pid}/fd/{fd}"))
        except FileNotFoundError:
            continue
    # The only socket is the control channel (a duplex Pipe is a socketpair):
    # no database, Redis or HTTP connection exists to be inherited by a fork.
    assert len([target for target in descriptors if target.startswith("socket:")]) == 1


def test_agent_child_runs_one_message_and_exits(agent_template: TemplateProcess) -> None:
    pid, work, result = agent_template.fork(worker_id="agent-ok")
    try:
        work.put({"message": {"run_id": "run-1"}})
        assert result.get(timeout=30) == {"ok": True}
        assert _wait_for_exit(agent_template, pid) == 0
    finally:
        work.close()
        result.close()


def test_agent_child_reports_a_run_error(agent_template: TemplateProcess) -> None:
    pid, work, result = agent_template.fork(worker_id="agent-error")
    try:
        work.put({"message": {"run_id": "run-2", "raise": "no such run"}})
        assert result.get(timeout=30) == {
            "ok": False, "error_type": "LookupError", "error": "no such run",
        }
        assert _wait_for_exit(agent_template, pid) == 0
    finally:
        work.close()
        result.close()


def test_agent_child_replies_and_exits_on_a_base_exception(agent_template: TemplateProcess) -> None:
    pid, work, result = agent_template.fork(worker_id="agent-exit")
    try:
        work.put({"message": {"run_id": "run-3", "exit": "stop"}})
        assert result.get(timeout=30) == {"ok": False, "error_type": "SystemExit", "error": "stop"}
        assert _wait_for_exit(agent_template, pid) == 0
    finally:
        work.close()
        result.close()


def _real_launcher(agent_template: TemplateProcess) -> Launcher:
    launcher = Launcher(
        Mock(send=AsyncMock()),
        identity=LauncherIdentity(
            launcher_id=uuid4(), incarnation_id=uuid4(),
            job_uid=None, pod_uid=None, hostname="agent-1", form="static",
        ),
        secret="test-secret",
    )
    launcher.session_generation = 1
    launcher.template = Mock(is_alive=Mock(return_value=True))
    launcher.agent_template = agent_template
    launcher.check_fork_pressure = Mock()
    return launcher


def _agent_run(route_id: str, sleep: float) -> dict:
    return {"id": route_id, "action": "agent_run", "payload": {
        "route_id": route_id, "run_id": f"run-{route_id}",
        "message": {"run_id": f"run-{route_id}", "sleep": sleep},
        "memory_request_bytes": 1,
    }}


async def _until(predicate, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(.05)


async def test_recycle_leaves_the_agent_template_and_its_children_alone(
    agent_template: TemplateProcess,
) -> None:
    launcher = _real_launcher(agent_template)
    template_pid = agent_template.pid
    await launcher.command(_agent_run("live", sleep=60), generation=1)
    child_pid = launcher.children["live"].pid
    try:
        await asyncio.wait_for(
            launcher.command({"id": "recycle", "action": "recycle", "payload": {}}, generation=1),
            timeout=5,
        )

        assert launcher.agent_template is agent_template
        assert agent_template.is_alive() and agent_template.pid == template_pid
        assert list(launcher.children) == ["live"]
        stat = proc_stat(child_pid)
        assert stat is not None and stat.state != "Z" and stat.ppid == template_pid
    finally:
        await launcher.terminate("live", "CancelledError", "Cancelled", grace=5)


def _set_child_subreaper(enabled: bool) -> None:
    # PR_SET_CHILD_SUBREAPER: orphans re-parent to this process, as they do to
    # the launcher when it runs as a pod's PID 1.
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.prctl(36, int(enabled), 0, 0, 0) == 0


async def test_monitor_reaps_an_agent_child_orphaned_by_its_template(
    agent_template: TemplateProcess,
) -> None:
    launcher = _real_launcher(agent_template)
    launcher.send = AsyncMock()
    _set_child_subreaper(True)
    monitor: asyncio.Task | None = None
    try:
        await launcher.command(_agent_run("orphan", sleep=1), generation=1)
        child_pid = launcher.children["orphan"].pid
        os.kill(agent_template.pid, 9)

        def orphan_state() -> str | None:
            stat = proc_stat(child_pid)
            return stat.state if stat is not None and stat.ppid == os.getpid() else None

        await _until(lambda: "orphan" not in launcher.children)
        await _until(lambda: orphan_state() == "Z")
        with patch("src.worker.remote.launcher.effective_memory_sample", return_value=(1, 100)):
            monitor = asyncio.create_task(launcher.monitor())
            await _until(lambda: proc_stat(child_pid) is None)
    finally:
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        _set_child_subreaper(False)


def test_workflow_template_does_not_freeze() -> None:
    template = TemplateProcess()
    template.start()
    try:
        assert template.profile == "workflow"
        assert template.frozen_objects == 0
    finally:
        template.shutdown()
