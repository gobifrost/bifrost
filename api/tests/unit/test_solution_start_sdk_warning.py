"""`bifrost solution start` must warn when the locally-installed web SDK is
stale relative to the server, without ever blocking startup on the check.

Rules (see sdk_staleness_warning docstring / task-6 brief):
- server_fp None (old server / fetch failure / "unavailable") -> silent.
- fingerprints equal -> silent (nothing changed -> never prompts).
- contracts both present and DIFFERENT -> loud "breaking" (red).
- fingerprints differ, contracts equal/missing -> gentle "warn" (yellow).
- installed_fp None (unstamped old SDK) with server_fp present -> "warn".
"""
import json

import yaml
from click.testing import CliRunner

from bifrost.commands.solution import sdk_staleness_warning, solution_group


class TestSdkStalenessWarningHelper:
    def test_server_fp_none_is_silent(self):
        assert sdk_staleness_warning("abc", None, 1, 1) is None

    def test_equal_fingerprints_is_silent(self):
        assert sdk_staleness_warning("abc", "abc", 1, 1) is None
        # Nothing changed -> never prompts, even if contract fields differ oddly.
        assert sdk_staleness_warning("abc", "abc", None, None) is None

    def test_differing_contracts_is_breaking(self):
        result = sdk_staleness_warning("abc", "def", 1, 2)
        assert result is not None
        message, severity = result
        assert severity == "breaking"
        assert "SDK contract v1" in message
        assert "server v2" in message
        assert "bifrost solution sdk update" in message
        assert "incompatible" in message

    def test_differing_fingerprints_same_contract_is_warn(self):
        result = sdk_staleness_warning("abc", "def", 1, 1)
        assert result is not None
        message, severity = result
        assert severity == "warn"
        assert "update available" in message.lower()
        assert "bifrost solution sdk update" in message

    def test_differing_fingerprints_missing_contract_is_warn(self):
        result = sdk_staleness_warning("abc", "def", None, 1)
        assert result is not None
        message, severity = result
        assert severity == "warn"

        result = sdk_staleness_warning("abc", "def", 1, None)
        assert result is not None
        _, severity = result
        assert severity == "warn"

    def test_unstamped_installed_sdk_with_server_fp_is_warn(self):
        result = sdk_staleness_warning(None, "def", None, 1)
        assert result is not None
        message, severity = result
        assert severity == "warn"
        assert "bifrost solution sdk update" in message


def _start_workspace(tmp_path, monkeypatch):
    """Minimal bound-workspace fixture mirroring test_solution_dev_command.py's
    `_start_workspace` helper: fakes the network/host layer so `start` can run
    all the way through in a CliRunner invocation."""
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
    app_dir = tmp_path / "apps" / "dash"
    app_dir.mkdir(parents=True)

    class _FakeClient:
        organization = {"id": "org-1"}
        user = {"id": "u", "is_superuser": True}
        api_url = "http://localhost:8000"
        _access_token = "tok"

    fake_client = _FakeClient()
    monkeypatch.setattr(client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: fake_client))
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
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: None)

    class _FakeProc:
        pid = 4242

    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: _FakeProc())

    served = {}

    async def _fake_serve(*args, **kwargs):
        served["called"] = True
        return None

    monkeypatch.setattr("bifrost.commands.solution._serve", _fake_serve)
    monkeypatch.setattr("bifrost.commands.solution._ensure_port_free", lambda port: None)
    monkeypatch.setattr("bifrost.commands.solution._wait_for_vite", lambda proc, port: None)
    monkeypatch.setattr("bifrost.commands.solution._terminate_process_group", lambda proc: None)

    return app_dir, served, fake_client


class TestStartWiresInStalenessWarning:
    def test_start_prints_warning_and_still_boots(self, tmp_path, monkeypatch):
        app_dir, served, fake_client = _start_workspace(tmp_path, monkeypatch)

        # Installed SDK is stamped and stale relative to the server.
        node_modules_bifrost = app_dir / "node_modules" / "bifrost"
        node_modules_bifrost.mkdir(parents=True)
        (node_modules_bifrost / "package.json").write_text(json.dumps({
            "bifrost": {"fingerprint": "old-fp", "contract": 1},
        }))

        class _VersionResp:
            status_code = 200

            def json(self):
                return {"sdk_fingerprint": "new-fp", "sdk_contract_version": 1}

        async def _fake_get(path, **kwargs):
            assert path == "/api/version"
            return _VersionResp()

        fake_client.get = _fake_get

        result = CliRunner().invoke(solution_group, ["start"])
        assert result.exit_code == 0, result.output
        assert "Web SDK update available" in result.output
        assert "bifrost solution sdk update" in result.output
        # Startup still proceeded to the serve step.
        assert served.get("called") is True

    def test_start_swallows_version_fetch_failure_silently(self, tmp_path, monkeypatch):
        app_dir, served, fake_client = _start_workspace(tmp_path, monkeypatch)

        node_modules_bifrost = app_dir / "node_modules" / "bifrost"
        node_modules_bifrost.mkdir(parents=True)
        (node_modules_bifrost / "package.json").write_text(json.dumps({
            "bifrost": {"fingerprint": "old-fp", "contract": 1},
        }))

        async def _fake_get(path, **kwargs):
            raise RuntimeError("network is down")

        fake_client.get = _fake_get

        result = CliRunner().invoke(solution_group, ["start"])
        assert result.exit_code == 0, result.output
        assert "Web SDK" not in result.output
        assert "sdk update" not in result.output
        assert served.get("called") is True
