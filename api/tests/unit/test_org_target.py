"""Unit tests for the shared CLI org-targeting standard (--org / --global)."""

import click
import pytest
from click.testing import CliRunner

from bifrost.org_target import OrgTarget, org_option, resolve_org_target


class _FakeResolver:
    async def resolve(self, kind, value):
        assert kind == "org"
        return f"uuid-for-{value}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "org,is_global,expected",
    [
        (None, False, OrgTarget.home()),  # omit -> home
        (None, True, OrgTarget.global_()),  # --global
        ("none", False, OrgTarget.global_()),  # --org none
        ("global", False, OrgTarget.global_()),  # --org global
        ("acme", False, OrgTarget.org("uuid-for-acme")),  # --org name
    ],
)
async def test_resolve_org_target(org, is_global, expected):
    got = await resolve_org_target(org, is_global, _FakeResolver())
    assert got == expected


@pytest.mark.asyncio
async def test_org_and_global_conflict():
    with pytest.raises(ValueError, match="mutually exclusive"):
        await resolve_org_target("acme", True, _FakeResolver())


def test_org_target_wire_forms():
    # GLOBAL -> explicit None + is_set; HOME -> UNSET; ORG -> the uuid.
    assert OrgTarget.global_().organization_id is None
    assert OrgTarget.global_().is_set is True
    assert OrgTarget.home().is_set is False
    assert OrgTarget.org("u").organization_id == "u"


def test_aliases_map_to_one_param():
    captured = {}

    @click.command()
    @org_option
    def cmd(org, is_global):
        captured["org"] = org
        captured["is_global"] = is_global

    r = CliRunner()
    assert r.invoke(cmd, ["--org", "acme"]).exit_code == 0
    assert captured == {"org": "acme", "is_global": False}
    assert r.invoke(cmd, ["--organization", "beta"]).exit_code == 0
    assert captured["org"] == "beta"
    assert r.invoke(cmd, ["--scope", "gamma"]).exit_code == 0
    assert captured["org"] == "gamma"
    assert r.invoke(cmd, ["--global"]).exit_code == 0
    assert captured == {"org": None, "is_global": True}
