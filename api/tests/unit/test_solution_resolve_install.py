"""CLI _resolve_target_install — a disconnected deploy must not silently
full-replace the wrong client's install when multiple org-scoped installs share
a slug (success-criteria §3.4, Codex G5)."""
from __future__ import annotations

import json
import io
import zipfile

import pytest
import yaml

from bifrost.commands.solution import _AmbiguousInstall, _resolve_target_install


def test_no_match_returns_none():
    # GLOBAL target (None) against an empty list -> no match.
    assert _resolve_target_install([], "mysol", None) is None


def test_single_global_match():
    installs = [{"id": "g1", "slug": "mysol", "organization_id": None}]
    # GLOBAL target (None) matches the org-NULL install.
    assert _resolve_target_install(installs, "mysol", None) == "g1"


def test_single_org_match():
    installs = [{"id": "o1", "slug": "mysol", "organization_id": "org-a"}]
    assert _resolve_target_install(installs, "mysol", "org-a") == "o1"


def test_org_scope_matches_only_the_deployers_org():
    """Codex R6-P1-b: an org-scoped deploy must target the resolved org's OWN
    install, never another org's same-slug install. A deploy targeting org-b must
    not full-replace org-a's install."""
    installs = [
        {"id": "o1", "slug": "mysol", "organization_id": "org-a"},
        {"id": "o2", "slug": "mysol", "organization_id": "org-b"},
    ]
    # Target org-a resolves to o1; target org-b resolves to o2.
    assert _resolve_target_install(installs, "mysol", "org-a") == "o1"
    assert _resolve_target_install(installs, "mysol", "org-b") == "o2"


def test_org_scope_no_match_in_target_org_returns_none():
    """org-a has an install, but the target is org-c → no match → the caller
    creates a fresh org-c install (no clobber of org-a)."""
    installs = [{"id": "o1", "slug": "mysol", "organization_id": "org-a"}]
    assert _resolve_target_install(installs, "mysol", "org-c") is None


def test_duplicate_org_installs_in_same_org_is_ambiguous():
    """Defense in depth: if (somehow) two installs of the same slug exist in the
    target org, refuse to guess."""
    installs = [
        {"id": "o1", "slug": "mysol", "organization_id": "org-a"},
        {"id": "o2", "slug": "mysol", "organization_id": "org-a"},
    ]
    with pytest.raises(_AmbiguousInstall) as e:
        _resolve_target_install(installs, "mysol", "org-a")
    assert "o1" in str(e.value) and "o2" in str(e.value)
    assert "--solution" in str(e.value)


def test_org_target_never_matches_global():
    """An ORG target (a concrete org id) must NOT match the GLOBAL install
    (organization_id None). Under the unified standard org and global are
    distinct resolved values — org → a uuid, global → None — so the equality can
    never collapse them (the old R7-P1-a `None == None` footgun is structurally
    gone)."""
    installs = [{"id": "g1", "slug": "mysol", "organization_id": None}]  # global
    assert _resolve_target_install(installs, "mysol", "org-a") is None


def test_scope_filters_out_wrong_scope():
    installs = [
        {"id": "g1", "slug": "mysol", "organization_id": None},   # global
        {"id": "o1", "slug": "mysol", "organization_id": "org-a"},  # org
    ]
    # An org target only sees the org install.
    assert _resolve_target_install(installs, "mysol", "org-a") == "o1"
    # A global target (None) only sees the global one.
    assert _resolve_target_install(installs, "mysol", None) == "g1"


def test_deploy_fails_loudly_when_install_list_fetch_fails(tmp_path, monkeypatch):
    """An explicit --solution must surface list failures and never create."""
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

    result = CliRunner().invoke(solution_group, ["deploy", "--solution", "s"])
    assert result.exit_code != 0
    assert "Failed to list installs (500)" in result.output
    assert "internal server error" in result.output
    assert "Failed to create install" not in result.output


def test_deploy_requires_bound_workspace(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import bifrost.client as client_mod
    from bifrost.commands.solution import solution_group

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text("slug: s\nname: S\n")

    class _FakeClient:
        async def get(self, path, **kwargs):
            raise AssertionError(f"unexpected GET {path}")

        async def post(self, path, **kwargs):
            raise AssertionError(f"unexpected POST {path}")

    monkeypatch.setattr(
        client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: _FakeClient())
    )

    result = CliRunner().invoke(solution_group, ["deploy"])

    assert result.exit_code != 0
    assert "not bound to an install" in result.output


# ── deploy version + --force (Task 21) ──────────────────────────────────────


class _Resp:
    def __init__(self, status_code: int, text: str = "", body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = text or json.dumps(self._body)

    def json(self):
        return self._body


class _DeployFakeClient:
    """Resolves the install by slug and records the deploy upload.

    Deploy is async: the POST returns 202 + a job id, then the CLI polls the
    deploy-jobs status endpoint. ``deploy_resp`` overrides the POST response (to
    exercise the synchronous 409 paths); the default POST enqueues and the poll
    GET reports the job ``succeeded``.
    """

    organization = {"id": "org-1"}

    def __init__(
        self,
        deploy_resp: _Resp | None = None,
        job_status: str = "succeeded",
        job_error: str = "boom",
    ):
        self.deploy_files: dict | None = None
        self.deploy_params: dict | None = None
        self._deploy_resp = deploy_resp or _Resp(
            202, body={"deploy_job_id": "job-1"}
        )
        self._job_status = job_status
        self._job_error = job_error

    async def get(self, path, **kwargs):
        if path == "/api/solutions":
            return _Resp(
                200,
                body={
                    "solutions": [
                        {"id": "inst-1", "slug": "s", "organization_id": "org-1"}
                    ]
                },
            )
        if path == "/api/solutions/deploy-jobs/job-1":
            return _Resp(
                200,
                body={
                    "status": self._job_status,
                    "error": self._job_error,
                    "result": {},
                },
            )
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path, **kwargs):
        if path == "/api/solutions/inst-1/deploy":
            self.deploy_files = kwargs.get("files")
            self.deploy_params = kwargs.get("params")
            return self._deploy_resp
        raise AssertionError(f"unexpected POST {path}")


def _deploy_workspace(tmp_path, monkeypatch, fake, descriptor_text: str):
    from click.testing import CliRunner

    import bifrost.client as client_mod
    from bifrost.commands.solution import solution_group

    monkeypatch.chdir(tmp_path)
    (tmp_path / "bifrost.solution.yaml").write_text(descriptor_text)
    (tmp_path / ".env").write_text(
        "BIFROST_SOLUTION_ID=inst-1\n"
        "BIFROST_SOLUTION_SLUG=s\n"
        "BIFROST_SOLUTION_ORG_ID=org-1\n"
        "BIFROST_SOLUTION_SCOPE=org\n"
    )
    monkeypatch.setattr(
        client_mod.BifrostClient, "get_instance", staticmethod(lambda **k: fake)
    )
    return CliRunner(), solution_group


def _deploy_descriptor(fake: _DeployFakeClient) -> dict:
    assert fake.deploy_files is not None
    file_tuple = fake.deploy_files["file"]
    with zipfile.ZipFile(io.BytesIO(file_tuple[1])) as zf:
        return yaml.safe_load(zf.read("bifrost.solution.yaml"))


def test_deploy_body_includes_descriptor_version(tmp_path, monkeypatch):
    fake = _DeployFakeClient()
    runner, grp = _deploy_workspace(
        tmp_path, monkeypatch, fake, "slug: s\nname: S\nversion: 1.2.3\n"
    )
    result = runner.invoke(grp, ["deploy"])
    assert result.exit_code == 0, result.output
    assert _deploy_descriptor(fake)["version"] == "1.2.3"
    assert fake.deploy_params == {"force": "false"}


def test_deploy_force_flag_sets_force_true(tmp_path, monkeypatch):
    fake = _DeployFakeClient()
    runner, grp = _deploy_workspace(
        tmp_path, monkeypatch, fake, "slug: s\nname: S\nversion: 1.2.3\n"
    )
    result = runner.invoke(grp, ["deploy", "--force"])
    assert result.exit_code == 0, result.output
    assert fake.deploy_params == {"force": "true"}


def test_deploy_no_descriptor_version_sends_null(tmp_path, monkeypatch):
    """A versionless descriptor deploys fine — version null, never a crash."""
    fake = _DeployFakeClient()
    runner, grp = _deploy_workspace(tmp_path, monkeypatch, fake, "slug: s\nname: S\n")
    result = runner.invoke(grp, ["deploy"])
    assert result.exit_code == 0, result.output
    assert "version" not in _deploy_descriptor(fake)


def test_deploy_downgrade_prints_detail_and_force_hint(tmp_path, monkeypatch):
    """The downgrade gate (Task 20) now surfaces as a FAILED deploy job; the CLI
    re-attaches the re-run-with---force hint when polling sees the error."""
    detail = (
        "bundle version 0.9.0 is older than installed 1.0.0; "
        "re-run with force to downgrade"
    )
    fake = _DeployFakeClient(job_status="failed", job_error=detail)
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


# ── resolve_install_id_for_workspace (audit F1/F2: local-exec data plane) ────


class _SyncResp:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class _SyncHTTP:
    def __init__(self, resp: _SyncResp):
        self._resp = resp

    def get(self, path, **kwargs):
        assert path == "/api/solutions"
        return self._resp


class _LocalRunFakeClient:
    """A client exposing the SYNC surface the local-exec helper uses."""

    def __init__(self, resp: _SyncResp, org_id: str | None = "org-1"):
        self._sync_http = _SyncHTTP(resp)
        self.organization = {"id": org_id} if org_id else None


def _write_workspace(tmp_path, scope="org", slug="mysol"):
    (tmp_path / "bifrost.solution.yaml").write_text(
        f"slug: {slug}\nname: S\nscope: {scope}\n"
    )
    return tmp_path


def test_resolve_install_id_resolves_org_install(tmp_path):
    from bifrost.commands.solution import resolve_install_id_for_workspace

    ws = _write_workspace(tmp_path, scope="org", slug="mysol")
    client = _LocalRunFakeClient(
        _SyncResp(200, {"solutions": [
            {"id": "inst-1", "slug": "mysol", "organization_id": "org-1"},
        ]}),
        org_id="org-1",
    )
    assert resolve_install_id_for_workspace(client, ws) == "inst-1"


def test_resolve_install_id_none_when_not_a_workspace(tmp_path):
    from bifrost.commands.solution import resolve_install_id_for_workspace

    # No descriptor at tmp_path.
    client = _LocalRunFakeClient(_SyncResp(200, {"solutions": []}))
    assert resolve_install_id_for_workspace(client, tmp_path) is None


def test_resolve_install_id_none_on_forbidden_list(tmp_path):
    """A non-admin dev whose /api/solutions is 403 degrades to None (the run
    proceeds against the _repo/ cascade exactly as before)."""
    from bifrost.commands.solution import resolve_install_id_for_workspace

    ws = _write_workspace(tmp_path)
    client = _LocalRunFakeClient(_SyncResp(403))
    assert resolve_install_id_for_workspace(client, ws) is None


def test_resolve_install_id_none_when_no_install_yet(tmp_path):
    from bifrost.commands.solution import resolve_install_id_for_workspace

    ws = _write_workspace(tmp_path, slug="mysol")
    client = _LocalRunFakeClient(
        _SyncResp(200, {"solutions": [
            {"id": "other", "slug": "different", "organization_id": "org-1"},
        ]}),
    )
    assert resolve_install_id_for_workspace(client, ws) is None


def test_resolve_install_id_none_on_ambiguous(tmp_path):
    """Two same-slug org installs in the caller's org → ambiguous → None (the
    helper never guesses; it just declines to scope rather than crash the run)."""
    from bifrost.commands.solution import resolve_install_id_for_workspace

    ws = _write_workspace(tmp_path, scope="org", slug="mysol")
    client = _LocalRunFakeClient(
        _SyncResp(200, {"solutions": [
            {"id": "i1", "slug": "mysol", "organization_id": "org-1"},
            {"id": "i2", "slug": "mysol", "organization_id": "org-1"},
        ]}),
        org_id="org-1",
    )
    assert resolve_install_id_for_workspace(client, ws) is None
