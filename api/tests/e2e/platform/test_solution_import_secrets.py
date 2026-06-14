"""E2E: full-backup zip import — decrypt secrets blob, per-key collision handling.

Task 13 of the Solutions success-criteria programme.

Contract under test:
- A full-backup zip (with .bifrost/secrets.enc) installs + fills config slots silently
  when the slot is empty.
- A collision (existing Config value for that key in the target org) refuses with 409
  unless replace_secrets=true.
- A wrong password refuses the WHOLE import with 422 — nothing lands (decrypt-before-
  deploy is the critical ordering contract).

make_full_backup_zip builds a real full-backup zip by:
  1. Creating a source solution with a declared config + set value via the
     make_solution_with_set_config helper from test_solution_export_full.py.
  2. GETting /export?mode=full&password=... to get real encrypted zip bytes.
This is the correct approach: we exercise the real export format, not a hand-assembled
blob, so the test proves the round-trip end to end.
"""
from __future__ import annotations

import io
import uuid
import zipfile
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.e2e

# Stable slug prefix for collision tests — both zips need the SAME slug.
_COLLISION_SLUG = f"import-sec-col-{uuid.uuid4().hex[:8]}"


def _upload_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip Content-Type so httpx sets it correctly for multipart."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _create_org(e2e_client, headers: dict[str, str]) -> str:
    """Create a fresh org and return its id."""
    domain = f"import-sec-{uuid.uuid4().hex[:8]}.test"
    r = e2e_client.post(
        "/api/organizations",
        headers=headers,
        json={"name": f"ImportSec Org {domain}", "domain": domain},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def make_org(e2e_client, platform_admin):
    """Factory: create a fresh org, return a SimpleNamespace with .id."""
    async def _make() -> SimpleNamespace:
        org_id = _create_org(e2e_client, platform_admin.headers)
        return SimpleNamespace(id=uuid.UUID(org_id))
    return _make


@pytest.fixture
def make_full_backup_zip(e2e_client, platform_admin, db_session):
    """Factory: produce a full-backup zip carrying the given config values.

    Steps:
    1. Create a source org.
    2. Create a solution with the requested slug (or a random one if not given).
    3. Declare the config key on the solution + set its value in the source org
       (matches what the export sees).
    4. Export with mode=full&password=... and return the zip bytes.

    Using the REAL export endpoint guarantees we exercise the real encrypted blob
    format (not a hand-assembled one), so the import path is tested end-to-end.

    When a ``source_solution_id`` is provided, skip solution creation and use that
    existing solution (for updating the value and re-exporting, e.g. collision
    test).
    """
    from src.core.security import encrypt_secret
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm.config import Config
    from src.models.orm.solution_config_schema import SolutionConfigSchema

    async def _make(
        values: dict[str, Any],
        password: str,
        slug: str | None = None,
        config_type: str = "secret",
        source_solution_id: str | None = None,
        source_org_id: str | None = None,
    ) -> tuple[bytes, str, str]:
        headers = platform_admin.headers

        if source_solution_id is not None:
            # Re-use an existing source solution: update the config values and
            # re-export. This lets the collision test produce two zips from the
            # SAME source solution (same config schema UUIDs) with different
            # values — avoiding the duplicate PK issue that would occur if two
            # separate source solutions each tried to insert the same deterministic
            # schema UUID.
            assert source_org_id is not None
            sol_id = uuid.UUID(source_solution_id)
            org_id = uuid.UUID(source_org_id)

            # Update each config value in-place.
            for key, value in values.items():
                is_secret = config_type == ConfigTypeEnum.SECRET.value
                stored = encrypt_secret(str(value)) if is_secret else str(value)
                # Upsert: find existing Config row for (key, org) and update.
                from sqlalchemy import select as sa_select
                existing = (
                    await db_session.execute(
                        sa_select(Config).where(
                            Config.key == key,
                            Config.organization_id == org_id,
                            Config.integration_id.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing.value = {"value": stored}
                    existing.config_type = (
                        ConfigTypeEnum.SECRET if is_secret else ConfigTypeEnum.STRING
                    )
                    existing.updated_by = "import-secrets-test-update"
                else:
                    db_session.add(
                        Config(
                            key=key,
                            value={"value": stored},
                            config_type=(
                                ConfigTypeEnum.SECRET if is_secret else ConfigTypeEnum.STRING
                            ),
                            organization_id=org_id,
                            updated_by="import-secrets-test-update",
                        )
                    )
            await db_session.commit()

        else:
            # Create a fresh source org + solution.
            src_org_id = _create_org(e2e_client, headers)
            actual_slug = slug or f"import-sec-{uuid.uuid4().hex[:8]}"
            r = e2e_client.post(
                "/api/solutions",
                headers=headers,
                json={
                    "slug": actual_slug,
                    "name": actual_slug.upper(),
                    "scope": "org",
                    "organization_id": src_org_id,
                },
            )
            assert r.status_code in (200, 201), r.text
            sol = r.json()
            sol_id = uuid.UUID(sol["id"])
            org_id = uuid.UUID(sol["organization_id"]) if sol.get("organization_id") else None

            # Declare each key + set each value in the source org.
            for position, (key, value) in enumerate(values.items()):
                schema_id = uuid.uuid5(
                    uuid.NAMESPACE_URL, f"{str(sol_id)}/configs/{key}"
                )
                decl = SolutionConfigSchema(
                    id=schema_id,
                    solution_id=sol_id,
                    key=key,
                    type=config_type,
                    required=False,
                    description=f"Config {key} for import-secrets test",
                    default=None,
                    position=position,
                )
                db_session.add(decl)

                is_secret = config_type == ConfigTypeEnum.SECRET.value
                stored = encrypt_secret(str(value)) if is_secret else str(value)
                db_session.add(
                    Config(
                        key=key,
                        value={"value": stored},
                        config_type=(
                            ConfigTypeEnum.SECRET if is_secret else ConfigTypeEnum.STRING
                        ),
                        organization_id=org_id,
                        updated_by="import-secrets-test",
                    )
                )
            await db_session.commit()

        # Export via the real endpoint — exercises the real blob format.
        export_r = e2e_client.get(
            f"/api/solutions/{sol_id}/export?mode=full&password={password}",
            headers=headers,
        )
        assert export_r.status_code == 200, export_r.text
        zip_bytes = export_r.content
        assert len(zip_bytes) > 0, "export returned empty body"
        assert ".bifrost/secrets.enc" in zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist(), (
            "full export must include .bifrost/secrets.enc"
        )
        return zip_bytes, str(sol_id), str(org_id)

    return _make


# ---------------------------------------------------------------------------
# Test 1: empty slot → fill silently
# ---------------------------------------------------------------------------

async def test_full_import_fills_empty_secret_slot(
    e2e_client, platform_admin, make_full_backup_zip, make_org
):
    """Installing a full-backup zip into an org that has NO existing value for the
    declared key must succeed and mark the key as set."""
    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    zip_bytes, _, _ = await make_full_backup_zip(values={"api_key": "xyz"}, password="pw")
    org = await make_org()

    r = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": ("s.zip", zip_bytes, "application/zip")},
        data={"organization_id": str(org.id), "password": "pw"},
    )
    assert r.status_code == 200, r.text
    sol_id = r.json()["id"]

    # Setup status must show api_key as set.
    setup_r = e2e_client.get(f"/api/solutions/{sol_id}/setup", headers=headers)
    assert setup_r.status_code == 200, setup_r.text
    items = setup_r.json()["items"]
    api_key_item = next((i for i in items if i["key"] == "api_key"), None)
    assert api_key_item is not None, "api_key should be declared after install"
    assert api_key_item["is_set"] is True, "api_key should be set after full-backup import"


# ---------------------------------------------------------------------------
# Test 2: collision without replace_secrets → 409, naming the key
# Test 3: collision with replace_secrets → 200
# ---------------------------------------------------------------------------

async def test_full_import_collision_refuses_without_replace_flag(
    e2e_client, platform_admin, make_full_backup_zip, make_org
):
    """Re-installing a full-backup zip into an org that ALREADY has a value for
    the key must refuse with 409 (naming the colliding key). With replace_secrets=true
    the same install must succeed."""
    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    # Both zips must install to the SAME slug in the SAME org so the second
    # install is a re-install that collides.  Both zips are produced from the
    # SAME source solution (same config schema UUIDs) so that re-installing z2
    # doesn't hit a (solution_id, key) unique constraint from a different schema
    # UUID trying to coexist with the z1 schema.
    slug = f"import-sec-col-{uuid.uuid4().hex[:8]}"

    # First install: api_key=EXISTING (fresh source solution with slug pinned).
    z1, src_sol_id, src_org_id = await make_full_backup_zip(
        values={"api_key": "EXISTING"}, password="pw", slug=slug
    )
    org = await make_org()
    files1 = {"file": ("s.zip", z1, "application/zip")}
    r0 = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files=files1,
        data={"organization_id": str(org.id), "password": "pw"},
    )
    assert r0.status_code == 200, r0.text

    # Second install: SAME source solution, updated value api_key=NEW → collision.
    # Re-using the same source solution keeps the config schema UUIDs identical,
    # so the deployer can upsert them without hitting the (solution_id, key)
    # unique index.
    z2, _, _ = await make_full_backup_zip(
        values={"api_key": "NEW"},
        password="pw",
        source_solution_id=src_sol_id,
        source_org_id=src_org_id,
    )
    files2 = {"file": ("s.zip", z2, "application/zip")}
    r = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files=files2,
        data={"organization_id": str(org.id), "password": "pw"},
    )
    assert r.status_code == 409, r.text
    # The error body must name the colliding key.
    assert "api_key" in r.text, f"expected 'api_key' in collision error, got: {r.text}"

    # Same zip again, but with replace_secrets=true → must succeed.
    files3 = {"file": ("s.zip", z2, "application/zip")}
    r2 = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files=files3,
        data={"organization_id": str(org.id), "password": "pw", "replace_secrets": "true"},
    )
    assert r2.status_code == 200, r2.text


# ---------------------------------------------------------------------------
# Test 4: wrong password → 422, and NOTHING lands
# ---------------------------------------------------------------------------

async def test_wrong_password_rejected(
    e2e_client, platform_admin, make_full_backup_zip, make_org
):
    """A wrong password must be rejected with 422, and nothing must be created
    (decrypt-before-deploy contract: if decrypt fails, nothing lands at all)."""
    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    slug = f"import-sec-badpw-{uuid.uuid4().hex[:8]}"
    zip_bytes, _, _ = await make_full_backup_zip(
        values={"api_key": "x"}, password="correct-pw", slug=slug
    )
    org = await make_org()

    # Wrong password → must fail.
    r = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files={"file": ("s.zip", zip_bytes, "application/zip")},
        data={"organization_id": str(org.id), "password": "WRONG"},
    )
    assert r.status_code == 422, r.text

    # CRITICAL CONTRACT: no solution with this slug must exist in the TARGET org.
    # (The source org has a solution with this slug from make_full_backup_zip, but
    # that's a different org — we check that the target org's copy doesn't exist.)
    list_r = e2e_client.get("/api/solutions", headers=headers)
    assert list_r.status_code == 200, list_r.text
    target_org_slugs = [
        s["slug"]
        for s in list_r.json()["solutions"]
        if s.get("organization_id") == str(org.id)
    ]
    assert slug not in target_org_slugs, (
        f"slug '{slug}' must not exist in target org after a wrong-password import, "
        f"but it was found — decrypt-before-deploy contract violated. "
        f"(Target org solutions: {target_org_slugs})"
    )
