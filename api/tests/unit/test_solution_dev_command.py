from pathlib import Path

import yaml
from click.testing import CliRunner

from bifrost.commands.solution import handle_solution, solution_group


def test_start_refuses_outside_solution_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no bifrost.solution.yaml here
    result = CliRunner().invoke(solution_group, ["start"])
    assert result.exit_code != 0
    assert "Solution workspace" in result.output or "solution init" in result.output


def test_set_dev_execution_context_sets_org(monkeypatch):
    from bifrost.solution_dev import function_host
    captured = {}

    # Patch the imported setter inside the function by patching the source module.
    import bifrost._context as _ctx_mod
    monkeypatch.setattr(_ctx_mod, "set_execution_context", lambda ctx: captured.__setitem__("ctx", ctx))

    function_host.set_dev_execution_context(
        user={"id": "u1", "email": "d@e.com", "name": "Dev", "is_superuser": True},
        org={"id": "org-123", "name": "Acme", "is_active": True, "is_provider": False},
    )
    assert captured["ctx"].scope == "org-123"
    assert captured["ctx"].is_platform_admin is True


def test_start_spawns_npm_via_resolved_path(tmp_path: Path, monkeypatch):
    # Windows: npm is `npm.cmd`. shutil.which honors PATHEXT but CreateProcess
    # (subprocess with a literal "npm" argv[0]) does not — so every npm spawn
    # must use the which() result, not the bare name.
    import shutil
    import subprocess

    import bifrost.client as client_mod
    import bifrost.commands.solution as solution_mod
    from bifrost.solution_dev import function_host

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        yaml.safe_dump({"apps": {
            "a": {"id": "a", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
        }})
    )
    (tmp_path / "apps" / "dash").mkdir(parents=True)

    class _FakeClient:
        organization = {"id": "org-1"}
        user = {"id": "u", "is_superuser": True}
        api_url = "http://localhost:8000"
        _access_token = "tok"

    monkeypatch.setattr(client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: _FakeClient()))
    monkeypatch.setattr(function_host, "set_dev_execution_context", lambda **k: None)

    class _FakeHost:
        def __init__(self, workspace):
            pass

        def reload(self):
            pass

        def refs(self):
            return []

    monkeypatch.setattr(function_host, "FunctionHost", _FakeHost)

    npm_path = r"C:\nodejs\npm.cmd"
    monkeypatch.setattr(shutil, "which", lambda name: npm_path if name == "npm" else None)

    spawned: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        spawned.append(list(argv))

    class _FakeProc:
        pid = 4242

    def _fake_popen(argv, **kwargs):
        spawned.append(list(argv))
        return _FakeProc()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    async def _fake_serve(*args, **kwargs):
        return None

    monkeypatch.setattr(solution_mod, "_serve", _fake_serve)
    monkeypatch.setattr(solution_mod, "_terminate_process_group", lambda proc: None)

    result = CliRunner().invoke(solution_group, ["start"])
    assert result.exit_code == 0, result.output
    # Both spawns (npm install + npm run dev) ran, each with the RESOLVED path.
    assert len(spawned) == 2
    for argv in spawned:
        assert argv[0] == npm_path, f"npm spawn used {argv[0]!r}, not the which() result"


def test_handle_solution_renders_clickexception_not_traceback(tmp_path, monkeypatch, capsys):
    # handle_solution dispatches with standalone_mode=False, which suppresses
    # click's own ClickException rendering — so it MUST catch ClickException and
    # show() it, else a handled error (e.g. ambiguous app) escapes as a raw
    # traceback. (This also covers deploy_cmd/install_cmd, which raise the same.)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        yaml.safe_dump({"apps": {
            "a": {"id": "a", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
            "b": {"id": "b", "slug": "admin", "path": "apps/admin", "app_model": "standalone_v2"},
        }})
    )

    # Stop before any network/auth: make app selection the first thing that runs
    # by faking an authenticated client. Patch BifrostClient.get_instance.
    import bifrost.client as client_mod

    class _FakeClient:
        organization = {"id": "org-1"}
        user = {"id": "u", "is_superuser": True}

    monkeypatch.setattr(client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: _FakeClient()))

    rc = handle_solution(["start"])  # two apps, no slug → AppSelectionError → ClickException
    out = capsys.readouterr()
    assert rc != 0
    # Rendered as a one-line error, not a Python traceback.
    assert "Traceback" not in out.err and "Traceback" not in out.out
    assert "Multiple apps" in out.err or "Multiple apps" in out.out
