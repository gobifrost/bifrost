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


# ── configs (omit=home / --global=null / --org=uuid) ────────────────────────


def test_configs_omit_is_home(monkeypatch):
    """Omit -> HOME: no organization_id key (server uses caller org)."""
    from bifrost.commands.configs import configs_group

    sent, res = _run(monkeypatch, configs_group, ["create", "--key", "K", "--value", "v"])
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert "organization_id" not in body


def test_configs_global_via_flag(monkeypatch):
    """--global -> explicit organization_id: null (global)."""
    from bifrost.commands.configs import configs_group

    sent, res = _run(
        monkeypatch, configs_group, ["create", "--key", "K", "--value", "v", "--global"]
    )
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert "organization_id" in body
    assert body["organization_id"] is None


def test_configs_org_via_name(monkeypatch):
    """--org acme -> resolved uuid."""
    from bifrost.commands.configs import configs_group

    sent, res = _run(
        monkeypatch, configs_group, ["create", "--key", "K", "--value", "v", "--org", "acme"]
    )
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert body["organization_id"] == "uuid-acme"


def test_configs_organization_synonym(monkeypatch):
    """--organization stays a synonym for --org."""
    from bifrost.commands.configs import configs_group

    sent, res = _run(
        monkeypatch,
        configs_group,
        ["create", "--key", "K", "--value", "v", "--organization", "beta"],
    )
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert body["organization_id"] == "uuid-beta"


def test_configs_set_omit_is_home(monkeypatch):
    """`set` upsert: omit -> HOME (no organization_id on the POST body)."""
    from bifrost.commands.configs import configs_group

    sent, res = _run(monkeypatch, configs_group, ["set", "K", "--value", "v"])
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert "organization_id" not in body


def test_configs_set_global(monkeypatch):
    from bifrost.commands.configs import configs_group

    sent, res = _run(monkeypatch, configs_group, ["set", "K", "--value", "v", "--global"])
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert body.get("organization_id", "MISSING") is None


# ── workflows register (omit=home / --global=null / --org=uuid) ─────────────


def test_workflows_register_omit_is_home(monkeypatch):
    """Omit -> HOME: no organization_id key (server fills caller org)."""
    from bifrost.commands.workflows import workflows_group

    sent, res = _run(
        monkeypatch,
        workflows_group,
        ["register", "--path", "functions/x.py", "--function-name", "main"],
    )
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert "organization_id" not in body


def test_workflows_register_global(monkeypatch):
    from bifrost.commands.workflows import workflows_group

    sent, res = _run(
        monkeypatch,
        workflows_group,
        ["register", "--path", "functions/x.py", "--function-name", "main", "--global"],
    )
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert body.get("organization_id", "MISSING") is None


def test_workflows_register_org_via_name(monkeypatch):
    from bifrost.commands.workflows import workflows_group

    sent, res = _run(
        monkeypatch,
        workflows_group,
        ["register", "--path", "functions/x.py", "--function-name", "main", "--org", "acme"],
    )
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert body["organization_id"] == "uuid-acme"


# ── tables / forms / agents / events (the entity --org standard) ────────────


def test_tables_create_omit_is_home(monkeypatch):
    from bifrost.commands.tables import tables_group

    sent, res = _run(monkeypatch, tables_group, ["create", "--name", "t1"])
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert "organization_id" not in body


def test_tables_create_global(monkeypatch):
    from bifrost.commands.tables import tables_group

    sent, res = _run(monkeypatch, tables_group, ["create", "--name", "t2", "--global"])
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert body.get("organization_id", "MISSING") is None


def test_tables_create_org_via_name(monkeypatch):
    from bifrost.commands.tables import tables_group

    sent, res = _run(monkeypatch, tables_group, ["create", "--name", "t3", "--org", "acme"])
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert body["organization_id"] == "uuid-acme"


def test_tables_create_org_none_is_global(monkeypatch):
    from bifrost.commands.tables import tables_group

    sent, res = _run(monkeypatch, tables_group, ["create", "--name", "t4", "--org", "none"])
    assert res.exit_code == 0
    _, body, _ = sent["post"]
    assert body.get("organization_id", "MISSING") is None


def test_forms_create_org_forms(monkeypatch):
    from bifrost.commands.forms import forms_group

    # omit -> home
    sent, res = _run(monkeypatch, forms_group, ["create", "--name", "f1"])
    assert res.exit_code == 0
    assert "organization_id" not in sent["post"][1]
    # --global -> null
    sent, res = _run(monkeypatch, forms_group, ["create", "--name", "f2", "--global"])
    assert res.exit_code == 0
    assert sent["post"][1].get("organization_id", "MISSING") is None
    # --org acme -> uuid
    sent, res = _run(monkeypatch, forms_group, ["create", "--name", "f3", "--org", "acme"])
    assert res.exit_code == 0
    assert sent["post"][1]["organization_id"] == "uuid-acme"


def test_agents_create_org_forms(monkeypatch):
    from bifrost.commands.agents import agents_group

    sent, res = _run(
        monkeypatch, agents_group, ["create", "--name", "a1", "--system-prompt", "hi"]
    )
    assert res.exit_code == 0
    assert "organization_id" not in sent["post"][1]
    sent, res = _run(
        monkeypatch,
        agents_group,
        ["create", "--name", "a2", "--system-prompt", "hi", "--global"],
    )
    assert res.exit_code == 0
    assert sent["post"][1].get("organization_id", "MISSING") is None
    sent, res = _run(
        monkeypatch,
        agents_group,
        ["create", "--name", "a3", "--system-prompt", "hi", "--org", "acme"],
    )
    assert res.exit_code == 0
    assert sent["post"][1]["organization_id"] == "uuid-acme"


def test_events_create_source_org_forms(monkeypatch):
    from bifrost.commands.events import events_group

    sent, res = _run(
        monkeypatch,
        events_group,
        ["create-source", "--name", "e1", "--source-type", "topic", "--event-type", "x.y"],
    )
    assert res.exit_code == 0
    assert "organization_id" not in sent["post"][1]
    sent, res = _run(
        monkeypatch,
        events_group,
        [
            "create-source", "--name", "e2", "--source-type", "topic",
            "--event-type", "x.y", "--global",
        ],
    )
    assert res.exit_code == 0
    assert sent["post"][1].get("organization_id", "MISSING") is None
    sent, res = _run(
        monkeypatch,
        events_group,
        [
            "create-source", "--name", "e3", "--source-type", "topic",
            "--event-type", "x.y", "--org", "acme",
        ],
    )
    assert res.exit_code == 0
    assert sent["post"][1]["organization_id"] == "uuid-acme"
