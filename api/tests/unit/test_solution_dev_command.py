from pathlib import Path

import yaml
from click.testing import CliRunner

from bifrost.commands.solution import handle_solution, solution_group


def test_solution_init_creates_remote_install_and_env(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)

    created_payloads = []

    class _Resp:
        status_code = 201
        text = ""

        def json(self):
            return {
                "id": "11111111-1111-1111-1111-111111111111",
                "slug": "dispatch",
                "organization_id": "22222222-2222-2222-2222-222222222222",
            }

    class _FakeClient:
        organization = {"id": "22222222-2222-2222-2222-222222222222"}

        async def post(self, path, json=None, **kwargs):
            assert path == "/api/solutions"
            created_payloads.append(json)
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["init", ".", "--slug", "dispatch", "--name", "Dispatch"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "bifrost.solution.yaml").is_file()
    env = (tmp_path / ".env").read_text()
    assert "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111" in env
    assert created_payloads[0]["slug"] == "dispatch"
    assert (
        created_payloads[0]["organization_id"]
        == "22222222-2222-2222-2222-222222222222"
    )


def test_solution_create_creates_remote_install_and_env(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    created_payloads = []

    class _Resp:
        status_code = 201
        text = ""

        def json(self):
            return {
                "id": "11111111-1111-1111-1111-111111111111",
                "slug": "dispatch",
                "organization_id": "22222222-2222-2222-2222-222222222222",
            }

    class _FakeClient:
        organization = {"id": "22222222-2222-2222-2222-222222222222"}

        async def post(self, path, json=None, **kwargs):
            assert path == "/api/solutions"
            created_payloads.append(json)
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["create", ".", "--slug", "dispatch", "--name", "Dispatch"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "bifrost.solution.yaml").is_file()
    env = (tmp_path / ".env").read_text()
    assert "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111" in env
    assert created_payloads[0]["slug"] == "dispatch"


def test_solution_create_global_scope(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    created_payloads = []

    class _Resp:
        status_code = 201
        text = ""

        def json(self):
            return {
                "id": "11111111-1111-1111-1111-111111111111",
                "slug": "dispatch",
                "organization_id": None,
            }

    class _FakeClient:
        organization = {"id": "22222222-2222-2222-2222-222222222222"}

        async def post(self, path, json=None, **kwargs):
            created_payloads.append(json)
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["create", ".", "--slug", "dispatch", "--name", "Dispatch", "--global"],
    )

    assert result.exit_code == 0, result.output
    assert created_payloads[0]["organization_id"] is None
    env = (tmp_path / ".env").read_text()
    assert "BIFROST_SOLUTION_SCOPE=global" in env


def test_solution_create_remote_failure_removes_new_descriptor(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)

    class _Resp:
        status_code = 500
        text = "boom"

    class _FakeClient:
        organization = {"id": "22222222-2222-2222-2222-222222222222"}

        async def post(self, path, json=None, **kwargs):
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["create", ".", "--slug", "dispatch", "--name", "Dispatch"],
    )

    assert result.exit_code != 0
    assert "Failed to create install: 500 boom" in result.output
    assert not (tmp_path / "bifrost.solution.yaml").exists()


def test_solution_create_binding_failure_keeps_descriptor(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)

    class _Resp:
        status_code = 201
        text = ""

        def json(self):
            return {
                "id": "11111111-1111-1111-1111-111111111111",
                "slug": "dispatch",
                "organization_id": "22222222-2222-2222-2222-222222222222",
            }

    class _FakeClient:
        organization = {"id": "22222222-2222-2222-2222-222222222222"}

        async def post(self, path, json=None, **kwargs):
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )
    monkeypatch.setattr(
        "bifrost.commands.solution.write_solution_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("readonly")),
    )

    result = CliRunner().invoke(
        solution_group,
        ["create", ".", "--slug", "dispatch", "--name", "Dispatch"],
    )

    assert result.exit_code != 0
    assert "Created Solution install 11111111-1111-1111-1111-111111111111" in result.output
    assert "failed to bind workspace in .env" in result.output
    assert (tmp_path / "bifrost.solution.yaml").is_file()


def test_solution_create_malformed_success_keeps_descriptor(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)

    class _Resp:
        status_code = 201
        text = "not json"

        def json(self):
            raise ValueError("bad json")

    class _FakeClient:
        organization = {"id": "22222222-2222-2222-2222-222222222222"}

        async def post(self, path, json=None, **kwargs):
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["create", ".", "--slug", "dispatch", "--name", "Dispatch"],
    )

    assert result.exit_code != 0
    assert "Created Solution install, but failed to read its binding" in result.output
    assert (tmp_path / "bifrost.solution.yaml").is_file()


def test_solution_bind_by_id_writes_env(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: dispatch\nname: Dispatch\n")

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "solutions": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "slug": "dispatch",
                        "organization_id": "22222222-2222-2222-2222-222222222222",
                    }
                ]
            }

    class _FakeClient:
        async def get(self, path, **kwargs):
            assert path == "/api/solutions"
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["bind", ".", "--solution", "11111111-1111-1111-1111-111111111111"],
    )

    assert result.exit_code == 0, result.output
    assert "Bound Solution install 11111111-1111-1111-1111-111111111111" in result.output
    env = (tmp_path / ".env").read_text()
    assert "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n" in env
    assert "BIFROST_SOLUTION_SLUG=dispatch\n" in env
    assert "BIFROST_SOLUTION_ORG_ID=22222222-2222-2222-2222-222222222222\n" in env
    assert "BIFROST_SOLUTION_SCOPE=org\n" in env


def test_solution_bind_by_slug_writes_env(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: dispatch\nname: Dispatch\n")

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "solutions": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "slug": "other",
                        "organization_id": None,
                    },
                    {
                        "id": "33333333-3333-3333-3333-333333333333",
                        "slug": "dispatch",
                        "organization_id": None,
                    },
                ]
            }

    class _FakeClient:
        async def get(self, path, **kwargs):
            assert path == "/api/solutions"
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["bind", ".", "--solution", "dispatch"],
    )

    assert result.exit_code == 0, result.output
    assert "Bound Solution install 33333333-3333-3333-3333-333333333333" in result.output
    env = (tmp_path / ".env").read_text()
    assert "BIFROST_SOLUTION_ID=33333333-3333-3333-3333-333333333333\n" in env
    assert "BIFROST_SOLUTION_SLUG=dispatch\n" in env
    assert "BIFROST_SOLUTION_ORG_ID=\n" in env
    assert "BIFROST_SOLUTION_SCOPE=global\n" in env


def test_solution_bind_refuses_slug_mismatch(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: dispatch\nname: Dispatch\n")

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "solutions": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "slug": "other",
                        "organization_id": None,
                    }
                ]
            }

    class _FakeClient:
        async def get(self, path, **kwargs):
            assert path == "/api/solutions"
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["bind", ".", "--solution", "11111111-1111-1111-1111-111111111111"],
    )

    assert result.exit_code != 0
    assert "does not match descriptor slug" in result.output
    assert not (tmp_path / ".env").exists()


def test_solution_bind_list_failure_is_surfaced(tmp_path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: dispatch\nname: Dispatch\n")

    class _Resp:
        status_code = 503
        text = "unavailable"

    class _FakeClient:
        async def get(self, path, **kwargs):
            assert path == "/api/solutions"
            return _Resp()

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(
        solution_group,
        ["bind", ".", "--solution", "dispatch"],
    )

    assert result.exit_code != 0
    assert "Failed to list installs (503): unavailable" in result.output
    assert not (tmp_path / ".env").exists()


def test_start_refuses_outside_solution_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no bifrost.solution.yaml here
    result = CliRunner().invoke(solution_group, ["start"])
    assert result.exit_code != 0
    assert "Solution workspace" in result.output or "solution init" in result.output


def test_start_refuses_unbound_solution_workspace(tmp_path: Path, monkeypatch):
    import bifrost.client as client_mod

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: dispatch\nname: Dispatch\n")

    class _FakeClient:
        organization = {"id": "org-1"}
        user = {"id": "u", "is_superuser": True}

    monkeypatch.setattr(
        client_mod.BifrostClient,
        "get_instance",
        staticmethod(lambda **kwargs: _FakeClient()),
    )

    result = CliRunner().invoke(solution_group, ["start"])

    assert result.exit_code != 0
    assert "not bound to an install" in result.output
    assert "bifrost solution bind --solution" in result.output


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
    from bifrost.solution_dev import function_host

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
        "BIFROST_SOLUTION_SLUG=s\n"
        "BIFROST_SOLUTION_ORG_ID=org-1\n"
        "BIFROST_SOLUTION_SCOPE=org\n"
    )
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

        def failures(self):
            return {}

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

    monkeypatch.setattr("bifrost.commands.solution._serve", _fake_serve)
    monkeypatch.setattr("bifrost.commands.solution._ensure_port_free", lambda port: None)
    monkeypatch.setattr("bifrost.commands.solution._wait_for_vite", lambda proc, port: None)
    monkeypatch.setattr(
        "bifrost.commands.solution._terminate_process_group", lambda proc: None
    )

    result = CliRunner().invoke(solution_group, ["start"])
    assert result.exit_code == 0, result.output
    # Both spawns (npm install + npm run dev) ran, each with the RESOLVED path.
    assert len(spawned) == 2
    for argv in spawned:
        assert argv[0] == npm_path, f"npm spawn used {argv[0]!r}, not the which() result"


def test_start_accepts_bind_host_and_public_url(tmp_path: Path, monkeypatch):
    import shutil
    import subprocess

    import bifrost.client as client_mod
    from bifrost.solution_dev import function_host

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
        "BIFROST_SOLUTION_SLUG=s\n"
        "BIFROST_SOLUTION_ORG_ID=org-1\n"
        "BIFROST_SOLUTION_SCOPE=org\n"
    )
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        yaml.safe_dump({"apps": {
            "a": {"id": "a", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
        }})
    )
    (tmp_path / "apps" / "dash").mkdir(parents=True)
    (tmp_path / "apps" / "dash" / "node_modules").mkdir()

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

        def failures(self):
            return {}

    monkeypatch.setattr(function_host, "FunctionHost", _FakeHost)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    popen_envs: list[dict[str, str]] = []

    class _FakeProc:
        pid = 4242

    def _fake_popen(argv, **kwargs):
        popen_envs.append(kwargs["env"])
        return _FakeProc()

    served: dict[str, object] = {}

    async def _fake_serve(
        client,
        chosen,
        org_info,
        host,
        port,
        vite_port,
        workspace,
        solution_id,
        bind_host,
        proxy_origin,
        global_repo_access,
    ):
        served["bind_host"] = bind_host
        served["port"] = port
        served["proxy_origin"] = proxy_origin

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr("bifrost.commands.solution._serve", _fake_serve)
    monkeypatch.setattr("bifrost.commands.solution._ensure_port_free", lambda port: None)
    monkeypatch.setattr("bifrost.commands.solution._wait_for_vite", lambda proc, port: None)
    monkeypatch.setattr(
        "bifrost.commands.solution._terminate_process_group", lambda proc: None
    )

    result = CliRunner().invoke(
        solution_group,
        [
            "start",
            "--host",
            "0.0.0.0",
            "--public-url",
            "http://devbox.test:3000/",
            "--port",
            "3000",
        ],
    )

    assert result.exit_code == 0, result.output
    assert served == {
        "bind_host": "0.0.0.0",
        "port": 3000,
        "proxy_origin": "http://devbox.test:3000",
    }
    assert popen_envs[0]["BIFROST_API_URL"] == "http://devbox.test:3000"


def test_handle_solution_renders_clickexception_not_traceback(tmp_path, monkeypatch, capsys):
    # handle_solution dispatches with standalone_mode=False, which suppresses
    # click's own ClickException rendering — so it MUST catch ClickException and
    # show() it, else a handled error (e.g. ambiguous app) escapes as a raw
    # traceback. (This also covers deploy_cmd/install_cmd, which raise the same.)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
        "BIFROST_SOLUTION_SLUG=s\n"
        "BIFROST_SOLUTION_ORG_ID=org-1\n"
        "BIFROST_SOLUTION_SCOPE=org\n"
    )
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


def test_dev_proxy_config_threads_descriptor_global_repo_access():
    from bifrost.commands.solution import _dev_proxy_config

    class _Client:
        api_url = "http://127.0.0.1:8000/"
        _access_token = "tok"

    class _Chosen:
        app_id = "app-uuid"

    cfg = _dev_proxy_config(
        _Client(), _Chosen(), {"id": "org-1"}, "install-1", True
    )
    assert cfg.global_repo_access is True
    assert cfg.upstream_url == "http://127.0.0.1:8000"
    assert cfg.solution_id == "install-1"
    assert cfg.org_id == "org-1"

    cfg = _dev_proxy_config(_Client(), _Chosen(), None, "install-1", False)
    assert cfg.global_repo_access is False
    assert cfg.org_id is None


def test_start_finds_solution_root_from_subdirectory(tmp_path, monkeypatch):
    """`bifrost solution start` from apps/<slug>/ must walk up to the root.

    scaffold-app and `bifrost run` already use find_solution_root; start
    requiring cwd == root was an inconsistency that blamed the user for
    being one directory deep (issue #462).
    """
    import shutil
    import subprocess

    import bifrost.client as client_mod
    from bifrost.solution_dev import function_host

    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
        "BIFROST_SOLUTION_SLUG=s\n"
        "BIFROST_SOLUTION_ORG_ID=org-1\n"
        "BIFROST_SOLUTION_SCOPE=org\n"
    )
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        yaml.safe_dump({"apps": {
            "a": {"id": "a", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
        }})
    )
    (tmp_path / "apps" / "dash").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "apps" / "dash")

    class _FakeClient:
        organization = {"id": "org-1"}
        user = {"id": "u", "is_superuser": True}
        api_url = "http://localhost:8000"
        _access_token = "tok"

    monkeypatch.setattr(client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: _FakeClient()))
    monkeypatch.setattr(function_host, "set_dev_execution_context", lambda **k: None)

    class _FakeHost:
        def __init__(self, workspace):
            self.workspace = workspace

        def reload(self):
            pass

        def refs(self):
            return []

        def failures(self):
            return {}

    hosts: list[object] = []
    def _make_host(workspace):
        host = _FakeHost(workspace)
        hosts.append(host)
        return host

    monkeypatch.setattr(function_host, "FunctionHost", _make_host)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: None)

    class _FakeProc:
        pid = 4242

    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: _FakeProc())

    async def _fake_serve(*args, **kwargs):
        return None

    monkeypatch.setattr("bifrost.commands.solution._serve", _fake_serve)
    monkeypatch.setattr("bifrost.commands.solution._ensure_port_free", lambda port: None)
    monkeypatch.setattr("bifrost.commands.solution._wait_for_vite", lambda proc, port: None)
    monkeypatch.setattr(
        "bifrost.commands.solution._terminate_process_group", lambda proc: None
    )

    result = CliRunner().invoke(solution_group, ["start"])
    assert result.exit_code == 0, result.output
    # The host was rooted at the SOLUTION ROOT, not the subdirectory cwd.
    assert hosts and str(hosts[0].workspace) == str(tmp_path.resolve())


def test_workspace_from_path_arg_walks_up_only_for_default(tmp_path, monkeypatch):
    """deploy/pull/bind share start's one-directory-deep friction: the implicit
    "." walks up to the root, an EXPLICIT path is honored as-is (review F5)."""
    from bifrost.commands.solution import _workspace_from_path_arg

    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\n")
    sub = tmp_path / "apps" / "dash"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    assert str(_workspace_from_path_arg(".")) == str(tmp_path.resolve())
    assert str(_workspace_from_path_arg(str(sub))) == str(sub.resolve())


def _start_workspace(tmp_path, monkeypatch):
    """Shared minimal start_cmd fixture: bound workspace + fakes for network
    and host. Returns the (monkeypatch-ready) `subprocess` module."""
    import shutil
    import subprocess

    import bifrost.client as client_mod
    from bifrost.solution_dev import function_host

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=11111111-1111-1111-1111-111111111111\n"
        "BIFROST_SOLUTION_SLUG=s\n"
        "BIFROST_SOLUTION_ORG_ID=org-1\n"
        "BIFROST_SOLUTION_SCOPE=org\n"
    )
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

        def failures(self):
            return {}

    monkeypatch.setattr(function_host, "FunctionHost", _FakeHost)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    return subprocess


def test_start_npm_install_failure_is_a_clean_click_error(tmp_path, monkeypatch):
    """A failed npm install must be a one-line ClickException, not a raw
    CalledProcessError traceback (issue #459) — first run on a fresh machine
    is exactly when the install is most likely to fail."""
    subprocess = _start_workspace(tmp_path, monkeypatch)

    def _failing_run(argv, **kwargs):
        raise subprocess.CalledProcessError(returncode=254, cmd=argv)

    monkeypatch.setattr(subprocess, "run", _failing_run)

    result = CliRunner().invoke(solution_group, ["start"])
    assert result.exit_code == 1, result.output
    assert "npm install failed" in result.output
    assert "Traceback" not in result.output


def test_wait_for_vite_fails_fast_when_process_dies(monkeypatch):
    """--strictPort makes vite exit when its port is taken; start must fail
    with the exit code instead of serving unexplained 502s (issue #460)."""
    import click
    import pytest as _pytest

    from bifrost.commands.solution import _wait_for_vite

    class _DeadProc:
        def poll(self):
            return 7

    with _pytest.raises(click.ClickException, match="exited with code 7"):
        _wait_for_vite(_DeadProc(), port=1)


def test_wait_for_vite_returns_when_port_accepts():
    import socket

    from bifrost.commands.solution import _wait_for_vite

    class _LiveProc:
        def poll(self):
            return None

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        _wait_for_vite(_LiveProc(), port=port, timeout=5)
    finally:
        server.close()


def test_wait_for_vite_times_out_with_clean_error():
    import socket

    import click
    import pytest as _pytest

    from bifrost.commands.solution import _wait_for_vite

    class _LiveProc:
        def poll(self):
            return None

    # A port nothing listens on: bind-then-close guarantees it's free.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    with _pytest.raises(click.ClickException, match="did not start listening"):
        _wait_for_vite(_LiveProc(), port=port, timeout=0.6)


def test_terminate_windows_tree_taskkills_the_whole_tree(monkeypatch):
    """On Windows there are no process groups: a bare terminate() reaps npm but
    orphans its vite child, which keeps the port bound and breaks the next
    start under --strictPort (issue #461). taskkill /T kills the tree."""
    import subprocess as _subprocess

    from bifrost.commands.solution import _terminate_windows_tree

    calls: list[list[str]] = []
    monkeypatch.setattr(
        _subprocess, "run", lambda argv, **k: calls.append(list(argv))
    )

    class _Proc:
        pid = 4242

    _terminate_windows_tree(_Proc())
    assert calls == [["taskkill", "/T", "/F", "/PID", "4242"]]


def test_ensure_port_free_rejects_an_occupied_port():
    """An orphaned vite from a previous run holding the port would make the
    new child die under --strictPort while readiness probes connect to the
    LEFTOVER — start must refuse BEFORE spawning (live-drive finding; the
    post-spawn grace re-check was a race against npm's cold start)."""
    import socket

    import click
    import pytest as _pytest

    from bifrost.commands.solution import _ensure_port_free

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        with _pytest.raises(click.ClickException, match="already in use"):
            _ensure_port_free(port)
    finally:
        server.close()


def test_ensure_port_free_passes_a_free_port():
    import socket

    from bifrost.commands.solution import _ensure_port_free

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    _ensure_port_free(port)  # must not raise
