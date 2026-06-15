"""E2E: the unified --org standard — install kind & entity scope chosen at
write time, derived from organization_id (no descriptor scope).

Proves the three wire states the standard guarantees, end-to-end against the
real server:

- HOME  (organization_id OMITTED)       -> the caller's own org.
- GLOBAL(organization_id explicit null)  -> global (organization_id NULL).
- ORG   (organization_id a UUID)         -> that org.

It exercises the two surfaces the standard rides on: ``POST /api/solutions``
(install kind) and ``POST /api/tables`` (entity scope). The same request DTO —
neither carries a ``scope`` input — yields a global OR an org result purely from
organization_id, and HOME/GLOBAL/ORG are three DISTINCT outcomes.

The caller's own org id is not hardcoded: HOME is asserted to be a stable,
non-null org that differs from both global (null) and an explicitly-targeted
other org — which is exactly the distinction the standard must preserve.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _make_org(e2e_client, headers) -> str:
    domain = f"orgstd-{uuid.uuid4().hex[:8]}.test"
    r = e2e_client.post(
        "/api/organizations",
        headers=headers,
        json={"name": f"OrgStd {domain}", "domain": domain},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Solutions: one (scope-less) descriptor, install kind from organization_id ─


def test_solution_install_kind_from_organization_id(e2e_client, platform_admin):
    headers = platform_admin.headers
    other_org = _make_org(e2e_client, headers)

    def create(extra: dict) -> dict:
        slug = f"orgstd-{uuid.uuid4().hex[:8]}"
        r = e2e_client.post(
            "/api/solutions",
            headers=headers,
            json={"slug": slug, "name": slug.upper(), **extra},
        )
        assert r.status_code in (200, 201), r.text
        return r.json()

    # GLOBAL: explicit null -> organization_id NULL, scope 'global'.
    glob = create({"organization_id": None})
    assert glob["organization_id"] is None
    assert glob["scope"] == "global"

    # ORG: explicit org uuid -> that org, scope 'org'.
    org = create({"organization_id": other_org})
    assert org["organization_id"] == other_org
    assert org["scope"] == "org"

    # HOME: omit organization_id -> the caller's own org (stable, non-null),
    # scope 'org'. It must differ from BOTH global (null) and the other org.
    home = create({})
    assert home["organization_id"] is not None
    assert home["organization_id"] != other_org
    assert home["scope"] == "org"
    # A second HOME create lands in the SAME org — HOME is the caller's own org,
    # not an accident.
    home2 = create({})
    assert home2["organization_id"] == home["organization_id"]


# ── Tables: entity scope from organization_id (home / global / org) ──────────


def _create_table(e2e_client, headers, body_extra: dict) -> dict:
    base = {
        "name": f"orgstd_{uuid.uuid4().hex[:8]}",
        "schema": {"columns": [{"name": "title", "type": "string"}]},
    }
    base.update(body_extra)
    r = e2e_client.post("/api/tables", headers=headers, json=base)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_table_scope_from_organization_id(e2e_client, platform_admin):
    headers = platform_admin.headers
    other_org = _make_org(e2e_client, headers)

    # HOME: omit -> caller's own org (stable, non-null).
    home = _create_table(e2e_client, headers, {})
    assert home["organization_id"] is not None

    # GLOBAL: explicit null -> organization_id NULL.
    glob = _create_table(e2e_client, headers, {"organization_id": None})
    assert glob["organization_id"] is None

    # ORG: explicit uuid -> that org.
    org = _create_table(e2e_client, headers, {"organization_id": other_org})
    assert org["organization_id"] == other_org

    # The three are distinct outcomes — the whole point of the standard.
    assert home["organization_id"] != glob["organization_id"]
    assert home["organization_id"] != org["organization_id"]
