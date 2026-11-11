"""Unit tests for the pure SCIM-lite mapping logic (no platform needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules"))

from idp_client import translate_keycloak_user
from scim_sync import (
    expand_grant,
    mapped_role_names,
    normalize_user_event,
    registry_id,
    resolve_grants,
    resolve_roles,
    roles_to_revoke,
)


def test_normalize_flat_payload():
    out = normalize_user_event(
        {
            "external_id": "idp-1",
            "email": "Ada@Example.com",
            "name": "Ada",
            "active": True,
            "groups": ["support-tier1", "support-tier1"],
        }
    )
    assert out == {
        "external_id": "idp-1",
        "email": "ada@example.com",
        "name": "Ada",
        "active": True,
        "groups": ["support-tier1"],
    }


def test_normalize_scim_keys_and_deactivate():
    out = normalize_user_event(
        {"id": "ext-9", "userName": "bob@example.com", "active": False}
    )
    assert out["external_id"] == "ext-9"
    assert out["name"] == "bob"
    assert out["active"] is False
    assert out["groups"] == []


def test_rejects_bad_email_and_org_choice():
    with pytest.raises(ValueError):
        normalize_user_event({"external_id": "x", "email": "not-an-email"})
    with pytest.raises(TypeError):
        normalize_user_event(
            {"external_id": "x", "email": "a@b.co", "active": "yes"}
        )
    # Org must never come from the payload: even a malicious key is ignored.
    out = normalize_user_event(
        {"external_id": "x", "email": "a@b.co", "organization_id": "other-org"}
    )
    assert "organization_id" not in out


def test_resolve_roles_drops_unmapped():
    assert resolve_roles(["a", "b"], {"a": "Support"}) == ["Support"]


def test_roles_to_revoke():
    assert roles_to_revoke(["Support", "Leads"], ["Support"]) == ["Leads"]
    assert roles_to_revoke(["Support"], ["Support"]) == []
    assert roles_to_revoke(["Support"], []) == ["Support"]


def test_translate_keycloak_user():
    out = translate_keycloak_user(
        {
            "id": "kc-1",
            "username": "ada",
            "email": "Ada@Example.com",
            "firstName": "Ada",
            "lastName": "Lovelace",
            "enabled": True,
        },
        ["eng"],
    )
    assert out["external_id"] == "kc-1"
    assert out["name"] == "Ada Lovelace"
    assert out["active"] is True
    assert out["groups"] == ["eng"]
    # Full normalize accepts translated output unchanged in shape.
    assert normalize_user_event(out)["email"] == "ada@example.com"


def test_translate_keycloak_defaults():
    out = translate_keycloak_user({"id": "kc-2", "username": "bob"}, [])
    assert out["active"] is True  # missing `enabled` means enabled
    assert out["email"] == "bob@unknown.invalid"
    assert normalize_user_event(out)["email"] == "bob@unknown.invalid"


def test_registry_id_namespaces_orgs():
    assert registry_id("org-a", "u1") == "org-a:u1"
    assert registry_id("org-a", "u1") != registry_id("org-b", "u1")


def test_expand_grant_shapes():
    assert expand_grant("Support") == ("Support", None)
    assert expand_grant({"role": "Support", "orgs": ["A", "B"]}) == ("Support", ["A", "B"])
    with pytest.raises(ValueError):
        expand_grant({"role": "Support", "orgs": []})
    with pytest.raises(ValueError):
        expand_grant({"role": "", "orgs": ["A"]})
    with pytest.raises(ValueError):
        expand_grant(42)


def test_resolve_grants_mixed():
    mapping = {"g1": "Support", "g2": {"role": "Leads", "orgs": ["A"]}, "gx": "Nope"}
    assert resolve_grants(["g1", "g2", "unmapped"], mapping) == [
        ("Support", None),
        ("Leads", ["A"]),
    ]


def test_mapped_role_names_include_ungranted_roles():
    # A role the mapping can grant stays managed even when the user's
    # current groups no longer name it; that is what makes revocation work.
    mapping = {"g1": "Support", "g2": {"role": "Leads", "orgs": ["A"]}}
    assert mapped_role_names(mapping) == {"Support", "Leads"}
    assert mapped_role_names({}) == set()
