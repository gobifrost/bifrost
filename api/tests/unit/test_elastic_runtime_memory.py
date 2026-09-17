from __future__ import annotations

import json

from scripts import elastic_runtime_memory as memory


def _load_module():
    return memory


def test_cgroup_v2_memory_is_labeled_from_current_peak_and_stat(tmp_path, monkeypatch):
    module = _load_module()
    cgroup_dir = tmp_path / "kubepods.slice" / "pod-a"
    cgroup_dir.mkdir(parents=True)
    (cgroup_dir / "memory.current").write_text("1234\n")
    (cgroup_dir / "memory.peak").write_text("5678\n")
    (cgroup_dir / "memory.max").write_text("max\n")
    (cgroup_dir / "memory.stat").write_text(
        "anon 100\nactive_file 25\ninactive_file 200\nkernel 7\n"
    )

    monkeypatch.setenv("BIFROST_MEMORY_DIAGNOSTIC_CGROUP_ROOT", str(tmp_path))
    monkeypatch.setattr(module, "_self_cgroup_relpaths", lambda: {"unified": "/kubepods.slice/pod-a"})

    result = module.read_cgroup_memory()

    assert result["version"] == "v2"
    assert result["current_bytes"] == 1234
    assert result["peak_bytes"] == 5678
    assert result["limit_bytes"] is None
    assert result["stat"]["kernel"] == 7
    assert result["working_set_estimate_bytes"] == 1034


def test_cgroup_reader_requires_v2(monkeypatch):
    module = _load_module()

    monkeypatch.setattr(module, "_self_cgroup_relpaths", lambda: {"memory": "/docker/abc"})

    try:
        module.read_cgroup_memory()
    except RuntimeError as exc:
        assert str(exc) == "cgroup_v2_unavailable"
    else:
        raise AssertionError("expected v2-only cgroup reader to reject non-v2 cgroups")


def test_stage_list_keeps_parent_imports_stdlib_only_for_python_baseline():
    module = _load_module()

    names = [stage.name for stage in module.STAGES]

    assert names[0] == "python"
    assert "construct-worker-consumers" in names
    assert "selected-platform-job" in names
    assert "application-sdk-update-module" in names


def test_error_output_is_sanitized_to_exception_type(monkeypatch, capsys):
    module = _load_module()
    stage = module.Stage(
        "boom",
        "boom",
        lambda _args: (_ for _ in ()).throw(RuntimeError("secret-token-value")),
    )

    monkeypatch.setattr(module, "STAGES", (stage,))
    monkeypatch.setattr(module, "collect_memory_sample", lambda: {"proc": {}, "cgroup": {}})

    args = type("Args", (), {"child_stage": "boom"})()

    assert module._run_child(args) == 1
    output = capsys.readouterr().out
    assert "secret-token-value" not in output
    payload = json.loads(output.split("=", 1)[1])
    assert payload["error"] == "RuntimeError"


def test_repo_pythonpath_uses_existing_env_when_script_is_copied(monkeypatch, tmp_path):
    module = _load_module()

    monkeypatch.setenv("PYTHONPATH", "/app/api:/app")
    monkeypatch.setattr(module, "__file__", str(tmp_path / "elastic_runtime_memory.py"))

    assert module._repo_pythonpath() == "/app/api:/app"
