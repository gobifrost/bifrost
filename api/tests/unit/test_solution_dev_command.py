from pathlib import Path

from click.testing import CliRunner

from bifrost.commands.solution import solution_group


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
