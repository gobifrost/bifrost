"""Tests for the host-side Docker worker memory probe."""

from __future__ import annotations

from types import SimpleNamespace

from scripts import worker_memory_probe as probe


def test_inspect_container_reads_only_approved_identity_fields(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout="4321\tcontainer-id\t/worker\timage-id\n",
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    container = probe.inspect_container("worker")

    assert container == probe.Container(
        requested="worker",
        host_pid=4321,
        container_id="container-id",
        name="/worker",
        image="image-id",
    )
    assert calls == [
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Pid}}\t{{.Id}}\t{{.Name}}\t{{.Image}}",
            "worker",
        ]
    ]


def test_sample_reads_cgroup_and_preserves_vanished_process_values(tmp_path) -> None:
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    (proc_root / "4321").mkdir(parents=True)
    (proc_root / "4321" / "cgroup").write_text("0::/docker/worker\n")
    cgroup = cgroup_root / "docker" / "worker"
    cgroup.mkdir(parents=True)
    (cgroup / "memory.current").write_text("1500\n")
    (cgroup / "memory.peak").write_text("2200\n")
    (cgroup / "memory.max").write_text("max\n")
    (cgroup / "memory.stat").write_text(
        "anon 300\nfile 200\ninactive_file 400\nkernel 50\n"
    )
    (cgroup / "cgroup.procs").write_text("11\n12\n")
    (cgroup / "cpu.stat").write_text("usage_usec 30\nuser_usec 20\nsystem_usec 10\nnr_periods 5\n")
    process = proc_root / "11"
    process.mkdir()
    (process / "comm").write_text("python\n")
    (process / "status").write_text("Name:\tpython\nState:\tS (sleeping)\nVmRSS:\t10 kB\n")
    (process / "smaps_rollup").write_text("Pss:\t8 kB\n")

    sample = probe.sample_container(
        probe.Container("worker", 4321, "id", "/worker", "image"),
        proc_root=proc_root,
        cgroup_root=cgroup_root,
    )

    assert sample["cpu"] == {"usage_usec": 30, "user_usec": 20, "system_usec": 10}
    assert sample["memory"] == {
        "current_bytes": 1500,
        "peak_bytes": 2200,
        "max_bytes": None,
        "stat": {
            "anon_bytes": 300,
            "file_bytes": 200,
            "inactive_file_bytes": 400,
            "kernel_bytes": 50,
        },
        "working_set_estimate_bytes": 1100,
    }
    assert sample["processes"] == [
        {
            "pid": 11,
            "comm": "python",
            "status": "S (sleeping)",
            "rss_bytes": 10 * 1024,
            "pss_bytes": 8 * 1024,
        },
        {
            "pid": 12,
            "comm": None,
            "status": None,
            "rss_bytes": None,
            "pss_bytes": None,
        },
    ]
