"""CLI _resolve_target_install — a disconnected deploy must not silently
full-replace the wrong client's install when multiple org-scoped installs share
a slug (success-criteria §3.4, Codex G5)."""
from __future__ import annotations

import json

import pytest

from bifrost.commands.solution import _AmbiguousInstall, _resolve_target_install


def test_no_match_returns_none():
    assert _resolve_target_install([], "mysol", "global", deployer_org_id=None) is None


def test_single_global_match():
    installs = [{"id": "g1", "slug": "mysol", "organization_id": None}]
    assert _resolve_target_install(installs, "mysol", "global", deployer_org_id="org-a") == "g1"


def test_single_org_match():
    installs = [{"id": "o1", "slug": "mysol", "organization_id": "org-a"}]
    assert _resolve_target_install(installs, "mysol", "org", deployer_org_id="org-a") == "o1"


def test_org_scope_matches_only_the_deployers_org():
    """Codex R6-P1-b: an org-scoped deploy must target the caller's OWN org
    install, never another client's same-slug install. A developer in org-b
    must not full-replace org-a's install."""
    installs = [
        {"id": "o1", "slug": "mysol", "organization_id": "org-a"},
        {"id": "o2", "slug": "mysol", "organization_id": "org-b"},
    ]
    # Deployer in org-a resolves to o1; deployer in org-b resolves to o2.
    assert _resolve_target_install(installs, "mysol", "org", deployer_org_id="org-a") == "o1"
    assert _resolve_target_install(installs, "mysol", "org", deployer_org_id="org-b") == "o2"


def test_org_scope_no_match_in_callers_org_returns_none():
    """org-a has an install, but the deployer is in org-c → no match → the
    caller creates a fresh org-c install (no clobber of org-a)."""
    installs = [{"id": "o1", "slug": "mysol", "organization_id": "org-a"}]
    assert _resolve_target_install(installs, "mysol", "org", deployer_org_id="org-c") is None


def test_duplicate_org_installs_in_same_org_is_ambiguous():
    """Defense in depth: if (somehow) two installs of the same slug exist in the
    caller's own org, refuse to guess."""
    installs = [
        {"id": "o1", "slug": "mysol", "organization_id": "org-a"},
        {"id": "o2", "slug": "mysol", "organization_id": "org-a"},
    ]
    with pytest.raises(_AmbiguousInstall) as e:
        _resolve_target_install(installs, "mysol", "org", deployer_org_id="org-a")
    assert "o1" in str(e.value) and "o2" in str(e.value)
    assert "--solution" in str(e.value)


def test_org_scope_with_none_deployer_org_does_not_match_global():
    """R7-P1-a regression: a `None` deployer org (provider/admin context with no
    active org) running an ORG-scoped deploy must NOT match the GLOBAL install.

    A global install has organization_id None; the org-scope equality
    `organization_id == deployer_org_id` would be `None == None` → True, so an
    org-scoped deploy could full-replace the global install. An org-scoped deploy
    with no deployer org matches nothing → the caller creates a fresh install,
    never clobbering global."""
    installs = [{"id": "g1", "slug": "mysol", "organization_id": None}]  # global
    assert (
        _resolve_target_install(installs, "mysol", "org", deployer_org_id=None) is None
    )


def test_scope_filters_out_wrong_scope():
    installs = [
        {"id": "g1", "slug": "mysol", "organization_id": None},   # global
        {"id": "o1", "slug": "mysol", "organization_id": "org-a"},  # org
    ]
    # Deploying the org-scoped descriptor must only see the org install.
    assert _resolve_target_install(installs, "mysol", "org", deployer_org_id="org-a") == "o1"
    # And the global descriptor only the global one.
    assert _resolve_target_install(installs, "mysol", "global", deployer_org_id="org-a") == "g1"


def test_deploy_fails_loudly_when_install_list_fetch_fails(tmp_path, monkeypatch):
    """A non-200 from GET /api/solutions must abort the deploy with a loud
    error — not silently treat the list as empty, attempt a fresh create, and
    surface a confusing downstream 409 ('Failed to create install')."""
    from click.testing import CliRunner

    import bifrost.client as client_mod
    from bifrost.commands.solution import solution_group

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\nscope: org\n")

    class _Resp:
        def __init__(self, status_code: int, text: str = "", body: dict | None = None):
            self.status_code = status_code
            self.text = text
            self._body = body or {}

        def json(self):
            return self._body

    class _FakeClient:
        organization = {"id": "org-1"}

        async def get(self, path, **kwargs):
            assert path == "/api/solutions"
            return _Resp(500, text="internal server error")

        async def post(self, path, **kwargs):
            # Mimic the confusing downstream failure the old code produced:
            # the slug already exists, so the blind create 409s.
            return _Resp(409, text="install already exists")

    monkeypatch.setattr(
        client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: _FakeClient())
    )

    result = CliRunner().invoke(solution_group, ["deploy"])
    assert result.exit_code != 0
    assert "Failed to list installs (500)" in result.output
    assert "internal server error" in result.output
    assert "Failed to create install" not in result.output


# ── deploy version + --force (Task 21) ──────────────────────────────────────


class _Resp:
    def __init__(self, status_code: int, text: str = "", body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = text or json.dumps(self._body)

    def json(self):
        return self._body


class _DeployFakeClient:
    """Resolves the install by slug and records the deploy request body."""

    organization = {"id": "org-1"}

    def __init__(self, deploy_resp: _Resp | None = None):
        self.deploy_body: dict | None = None
        self._deploy_resp = deploy_resp or _Resp(
            200, body={"workflows_upserted": 0, "workflows_deleted": 0}
        )

    async def get(self, path, **kwargs):
        assert path == "/api/solutions"
        return _Resp(
            200,
            body={"solutions": [{"id": "inst-1", "slug": "s", "organization_id": "org-1"}]},
        )

    async def post(self, path, **kwargs):
        if path == "/api/solutions/inst-1/deploy":
            self.deploy_body = kwargs.get("json")
            return self._deploy_resp
        raise AssertionError(f"unexpected POST {path}")


def _deploy_workspace(tmp_path, monkeypatch, fake, descriptor_text: str):
    from click.testing import CliRunner

    import bifrost.client as client_mod
    from bifrost.commands.solution import solution_group

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text(descriptor_text)
    monkeypatch.setattr(
        client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: fake)
    )
    return CliRunner(), solution_group


def test_deploy_body_includes_descriptor_version(tmp_path, monkeypatch):
    fake = _DeployFakeClient()
    runner, grp = _deploy_workspace(
        tmp_path, monkeypatch, fake, "slug: s\nname: S\nversion: 1.2.3\n"
    )
    result = runner.invoke(grp, ["deploy"])
    assert result.exit_code == 0, result.output
    assert fake.deploy_body is not None
    assert fake.deploy_body["version"] == "1.2.3"
    assert fake.deploy_body["force"] is False


def test_deploy_force_flag_sets_force_true(tmp_path, monkeypatch):
    fake = _DeployFakeClient()
    runner, grp = _deploy_workspace(
        tmp_path, monkeypatch, fake, "slug: s\nname: S\nversion: 1.2.3\n"
    )
    result = runner.invoke(grp, ["deploy", "--force"])
    assert result.exit_code == 0, result.output
    assert fake.deploy_body is not None
    assert fake.deploy_body["force"] is True


def test_deploy_no_descriptor_version_sends_null(tmp_path, monkeypatch):
    """A versionless descriptor deploys fine — version null, never a crash."""
    fake = _DeployFakeClient()
    runner, grp = _deploy_workspace(tmp_path, monkeypatch, fake, "slug: s\nname: S\n")
    result = runner.invoke(grp, ["deploy"])
    assert result.exit_code == 0, result.output
    assert fake.deploy_body is not None
    assert fake.deploy_body.get("version") is None


def test_deploy_downgrade_409_prints_detail_and_force_hint(tmp_path, monkeypatch):
    """The downgrade 409 (Task 20 gate) surfaces the server detail PLUS a
    re-run-with---force hint."""
    detail = (
        "bundle version 0.9.0 is older than installed 1.0.0; "
        "re-run with force to downgrade"
    )
    fake = _DeployFakeClient(deploy_resp=_Resp(409, body={"detail": detail}))
    runner, grp = _deploy_workspace(
        tmp_path, monkeypatch, fake, "slug: s\nname: S\nversion: 0.9.0\n"
    )
    result = runner.invoke(grp, ["deploy"])
    assert result.exit_code != 0
    assert detail in result.output
    assert "--force" in result.output


def test_deploy_other_409_unchanged(tmp_path, monkeypatch):
    """Non-downgrade 409s keep the generic 'Deploy failed' path — no hint."""
    fake = _DeployFakeClient(deploy_resp=_Resp(409, body={"detail": "slug conflict"}))
    runner, grp = _deploy_workspace(
        tmp_path, monkeypatch, fake, "slug: s\nname: S\nversion: 1.0.0\n"
    )
    result = runner.invoke(grp, ["deploy"])
    assert result.exit_code != 0
    assert "Deploy failed: 409" in result.output
    assert "--force" not in result.output
