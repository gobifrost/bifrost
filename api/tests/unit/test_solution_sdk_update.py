import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from bifrost.commands.solution import solution_group


def _write_installed_sdk(app_dir: Path, fingerprint: str | None) -> None:
    pkg_dir = app_dir / "node_modules" / "bifrost"
    pkg_dir.mkdir(parents=True)
    pkg = {"name": "bifrost", "version": "1.0.0"}
    if fingerprint:
        pkg["bifrost"] = {"fingerprint": fingerprint}
    (pkg_dir / "package.json").write_text(json.dumps(pkg))


def test_installed_sdk_fingerprint_reads_stamp(tmp_path):
    from bifrost.commands.solution import installed_sdk_fingerprint

    _write_installed_sdk(tmp_path, "abcd1234abcd1234")
    assert installed_sdk_fingerprint(tmp_path) == "abcd1234abcd1234"


def test_installed_sdk_fingerprint_none_when_missing(tmp_path):
    from bifrost.commands.solution import installed_sdk_fingerprint

    assert installed_sdk_fingerprint(tmp_path) is None          # no node_modules
    _write_installed_sdk(tmp_path, None)                        # unstamped (old SDK)
    assert installed_sdk_fingerprint(tmp_path) is None


def _sdk_update_workspace(tmp_path, monkeypatch, *, installed_fingerprint):
    """Bound workspace with a single standalone_v2 app, mirroring
    `_start_workspace` in test_solution_dev_command.py but scoped to what
    `sdk update` needs: no FunctionHost/vite fakes, just client + app dir."""
    import bifrost.client as client_mod

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
    if installed_fingerprint is not None:
        _write_installed_sdk(app_dir, installed_fingerprint)

    class _FakeClient:
        organization = {"id": "org-1"}
        user = {"id": "u", "is_superuser": True}
        api_url = "http://localhost:8000"
        _access_token = "tok"

        async def get(self, path, **kwargs):
            assert path == "/api/version"
            return _VersionResp()

    monkeypatch.setattr(client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: _FakeClient()))
    return app_dir


class _VersionResp:
    status_code = 200
    text = ""

    def json(self):
        return {"sdk_fingerprint": "newnewnewnewnew1"}


def test_sdk_update_skips_when_current(tmp_path, monkeypatch):
    import subprocess

    app_dir = _sdk_update_workspace(tmp_path, monkeypatch, installed_fingerprint="newnewnewnewnew1")

    spawned = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: spawned.append(list(argv)))

    result = CliRunner().invoke(solution_group, ["sdk", "update", "."])

    assert result.exit_code == 0, result.output
    assert "already up to date" in result.output
    assert spawned == []  # npm NOT invoked


def test_sdk_update_reinstalls_and_verifies(tmp_path, monkeypatch):
    import subprocess

    app_dir = _sdk_update_workspace(tmp_path, monkeypatch, installed_fingerprint="oldoldoldoldold1")

    spawned = []

    def _fake_run(argv, **kwargs):
        spawned.append(list(argv))
        # Simulate npm install replacing node_modules/bifrost with the new SDK.
        import shutil as _shutil
        bifrost_dir = app_dir / "node_modules" / "bifrost"
        if bifrost_dir.exists():
            _shutil.rmtree(bifrost_dir)
        _write_installed_sdk(app_dir, "newnewnewnewnew1")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    result = CliRunner().invoke(solution_group, ["sdk", "update", "."])

    assert result.exit_code == 0, result.output
    assert spawned  # npm WAS invoked
    assert "oldoldoldoldold1" in result.output
    assert "newnewnewnewnew1" in result.output


def test_sdk_update_fails_loud_when_still_stale(tmp_path, monkeypatch):
    import subprocess

    app_dir = _sdk_update_workspace(tmp_path, monkeypatch, installed_fingerprint="oldoldoldoldold1")

    def _noop_run(argv, **kwargs):
        # npm install is mocked as a no-op: node_modules/bifrost keeps the old stamp.
        pass

    monkeypatch.setattr(subprocess, "run", _noop_run)

    result = CliRunner().invoke(solution_group, ["sdk", "update", "."])

    assert result.exit_code == 1
    assert "oldoldoldoldold1" in result.output
    assert "newnewnewnewnew1" in result.output
