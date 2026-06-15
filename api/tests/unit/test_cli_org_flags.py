"""Per-command tests that the unified --org standard sends the right org target.

The crux: HOME (omit) must send NO organization_id (server uses caller org);
GLOBAL (--global / --org none|global) sends an explicit null; --org <id|name>
sends the resolved uuid. Claims are org-only (global rejected).
"""

from __future__ import annotations

from click.testing import CliRunner


class _Resp:
    status_code = 200

    def json(self):
        return []

    text = ""

    def raise_for_status(self):
        return None


class _FakeClient:
    organization = {"id": "home-org"}

    def __init__(self, sent: dict):
        self._sent = sent

    async def get(self, path, params=None):
        self._sent["get"] = (path, params)
        return _Resp()

    async def post(self, path, json=None, params=None):
        self._sent["post"] = (path, json, params)
        return _Resp()

    async def put(self, path, json=None, params=None):
        self._sent["put"] = (path, json, params)
        return _Resp()

    async def patch(self, path, json=None, params=None):
        self._sent["patch"] = (path, json, params)
        return _Resp()

    async def delete(self, path, params=None):
        self._sent["delete"] = (path, params)
        return _Resp()


def _run(monkeypatch, group, argv):
    sent: dict = {}
    import bifrost.client as bc

    monkeypatch.setattr(
        bc.BifrostClient, "get_instance", staticmethod(lambda **k: _FakeClient(sent))
    )
    import bifrost.refs as rf

    async def _resolve(self, kind, value):
        return f"uuid-{value}"

    monkeypatch.setattr(rf.RefResolver, "resolve", _resolve)
    result = CliRunner().invoke(group, argv, catch_exceptions=False)
    return sent, result


# ── claims (org-only; global rejected) ──────────────────────────────────────


def test_claims_list_omit_is_home(monkeypatch):
    from bifrost.commands.claims import claims_group

    sent, res = _run(monkeypatch, claims_group, ["list"])
    assert res.exit_code == 0
    path, params = sent["get"]
    assert params == {}  # HOME -> no scope param


def test_claims_list_org_sends_scope(monkeypatch):
    from bifrost.commands.claims import claims_group

    sent, res = _run(monkeypatch, claims_group, ["list", "--org", "acme"])
    assert res.exit_code == 0
    _, params = sent["get"]
    assert params == {"scope": "uuid-acme"}


def test_claims_organization_synonym(monkeypatch):
    from bifrost.commands.claims import claims_group

    sent, res = _run(monkeypatch, claims_group, ["list", "--organization", "acme"])
    assert res.exit_code == 0
    assert sent["get"][1] == {"scope": "uuid-acme"}


def test_claims_global_rejected(monkeypatch):
    from bifrost.commands.claims import claims_group

    _, res = _run(monkeypatch, claims_group, ["list", "--global"])
    assert res.exit_code != 0
    assert "always org-scoped" in res.output
