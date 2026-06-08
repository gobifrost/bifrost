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
