"""Contracts for the bounded lean runner import/construction diagnostic."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import lean_runner_spike as spike


def test_parent_module_import_keeps_heavy_runtime_modules_out_of_fresh_interpreter():
    """The controller module must remain safe to host beside a warm template."""
    api_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; import scripts.lean_runner_spike; "
                "print(json.dumps(sorted(sys.modules)))"
            ),
        ],
        cwd=api_root,
        env={**os.environ, "PYTHONPATH": str(api_root)},
        check=True,
        capture_output=True,
        text=True,
    )

    modules = set(json.loads(result.stdout))
    assert not spike._forbidden_parent_modules(modules)


def test_canonical_inventory_metadata_resolves_same_definition_as_eager_registry():
    """Direct probes are constrained to references the canonical helper emitted."""
    inventory = spike.get_canonical_job_inventory(timeout_seconds=30)

    assert len({item["job_type"] for item in inventory}) == len(inventory)
    assert {item["job_type"] for item in inventory} >= {
        "application.deploy",
        "agent.summary_backfill",
        "solution.deploy",
    }
    assert all(
        set(item) == {"job_type", "module", "definition_name", "payload_model", "handler"}
        for item in inventory
    )

    selected = next(item for item in inventory if item["job_type"] == "application.deploy")
    result = spike.launch_probe_child(
        "selected",
        metadata=selected,
        timeout_seconds=30,
        hold_seconds=0,
    )

    assert result["ok"]
    assert result["comparison"]["matches_canonical_helper"]
    assert result["comparison"]["job_type"] == "application.deploy"

    with pytest.raises(ValueError, match="noncanonical_job_reference"):
        spike.launch_probe_child(
            "selected",
            metadata={
                **selected,
                "module": "scripts.lean_runner_spike",
            },
            timeout_seconds=30,
        )


def test_probe_child_failure_timeout_cleanup_and_sampling_math():
    """Failures keep samples, while a timed-out process group is reaped."""
    failure = spike.launch_probe_child("fail", timeout_seconds=5, hold_seconds=0)
    assert not failure["ok"]
    assert failure["error"] == "RuntimeError"
    assert failure["returncode"] is not None
    assert failure["samples"]

    timed_out = spike.launch_probe_child("timeout", hold_seconds=0)
    assert not timed_out["ok"]
    assert timed_out["error"] == "TimeoutExpired"
    assert timed_out["process_group_reaped"]
    assert timed_out["returncode"] is not None
    assert timed_out["samples"]
    assert any(sample["label"] == "child-active" for sample in timed_out["samples"])

    assert spike.sample_delta(
        {"proc": {"rss_bytes": 10, "pss_bytes": None}, "cgroup": {"working_set_estimate_bytes": 20}},
        {"proc": {"rss_bytes": 17, "pss_bytes": 5}, "cgroup": {"working_set_estimate_bytes": 13}},
    ) == {
        "rss_bytes": 7,
        "pss_bytes": None,
        "working_set_estimate_bytes": -7,
    }


def test_cycle_shuts_down_template_after_success_failure_and_timeout(monkeypatch):
    """Each cycle releases the real-template wrapper even when a probe fails."""
    templates: list[object] = []

    class FakeTemplate:
        def __init__(self) -> None:
            self.started = False
            self.shutdowns = 0
            self.pid = 4321

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return self.started

        def shutdown(self) -> None:
            self.shutdowns += 1
            self.started = False

    def make_template() -> FakeTemplate:
        template = FakeTemplate()
        templates.append(template)
        return template

    monkeypatch.setattr(spike, "get_canonical_job_inventory", lambda **_kwargs: [])
    monkeypatch.setattr(
        spike,
        "launch_probe_child",
        lambda stage, **_kwargs: {
            "stage": stage,
            "ok": stage == "heavy-payload",
            "error": None if stage == "heavy-payload" else "RuntimeError",
            "samples": [],
            "process_group_reaped": True,
        },
    )

    result = spike.run_cycle(
        template_factory=make_template,
        job_filters=[],
        mode="both",
        timeout_seconds=1,
        hold_seconds=0,
    )

    assert templates[0].shutdowns == 1
    assert result["template"]["recovered"]
    assert result["probes"]


def test_platform_runner_child_preserves_nonzero_main_return(monkeypatch, capsys):
    """The opt-in live bridge reports a runner refusal without exposing its lease."""
    ready_read, ready_write = os.pipe()
    active_read, active_write = os.pipe()
    try:
        monkeypatch.setattr(
            spike,
            "_read_child_metadata",
            lambda _fd: {"job_id": "job", "lease_token": "lease"},
        )
        monkeypatch.setattr(spike, "_child_ready_then_hold", lambda *_args: None)

        async def nonzero_runner(_job_id: str, _lease_token: str) -> int:
            return 1

        monkeypatch.setattr(
            spike,
            "_run_child_stage",
            lambda *_args: {"platform_runner": nonzero_runner},
        )
        args = SimpleNamespace(
            _metadata_fd=ready_read,
            _ready_fd=ready_write,
            _release_fd=ready_read,
            _active_fd=active_write,
            _child_stage="platform-runner",
        )

        assert spike._child_main(args) == 1
        payload = json.loads(capsys.readouterr().out.split("=", 1)[1])
        assert payload["ok"] is False
        assert payload["error"] == "PlatformRunnerFailed"
        assert payload["runner_returncode"] == 1
        assert payload["elapsed_ms"] >= 0
    finally:
        for fd in (ready_read, ready_write, active_read, active_write):
            try:
                os.close(fd)
            except OSError:
                # The child or an earlier cleanup step may already have closed this descriptor.
                pass
