"""The agent template profile: warm agent runtime, one forked child per run.

Real template processes (fork behavior can't be mocked). The forked child's
``run_agent_message`` is replaced by a stub through a ``sitecustomize`` module
on the template's ``PYTHONPATH``, so these tests exercise the fork protocol
without a database or a model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from src.services.execution.template_process import TemplateProcess

pytestmark = pytest.mark.slow

_STUB = textwrap.dedent(
    """
    import src.jobs.agent_run_child as agent_run_child


    async def run_agent_message(message):
        if "raise" in message:
            raise LookupError(message["raise"])


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
    pytest.fail(f"agent child {pid} did not exit")


def test_agent_template_is_ready_with_a_frozen_heap(agent_template: TemplateProcess) -> None:
    assert agent_template.is_alive()
    assert agent_template.profile == "agent"
    assert agent_template.frozen_objects is not None and agent_template.frozen_objects > 0
    assert agent_template.idle_seconds == 300


def test_agent_template_configures_mappers_without_loading_the_api_app() -> None:
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
        from sqlalchemy import inspect
        from src.models.orm.agent_runs import AgentRun
        print(json.dumps({
            "main_loaded": "src.main" in sys.modules,
            "mappers_configured": inspect(AgentRun).configured,
        }))
    ''')
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "main_loaded": False, "mappers_configured": True,
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


def test_workflow_template_does_not_freeze_or_report_an_idle_period() -> None:
    template = TemplateProcess()
    template.start()
    try:
        assert template.profile == "workflow"
        assert template.frozen_objects == 0
        assert template.idle_seconds is None
    finally:
        template.shutdown()
