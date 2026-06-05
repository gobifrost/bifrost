"""CLI _resolve_target_install — a disconnected deploy must not silently
full-replace the wrong client's install when multiple org-scoped installs share
a slug (success-criteria §3.4, Codex G5)."""
from __future__ import annotations

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


def test_scope_filters_out_wrong_scope():
    installs = [
        {"id": "g1", "slug": "mysol", "organization_id": None},   # global
        {"id": "o1", "slug": "mysol", "organization_id": "org-a"},  # org
    ]
    # Deploying the org-scoped descriptor must only see the org install.
    assert _resolve_target_install(installs, "mysol", "org", deployer_org_id="org-a") == "o1"
    # And the global descriptor only the global one.
    assert _resolve_target_install(installs, "mysol", "global", deployer_org_id="org-a") == "g1"
