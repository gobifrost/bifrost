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
  2. POSTing /export?mode=full (password in body) to get real encrypted zip bytes.
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

from tests.e2e.platform.conftest import wait_for_install

pytestmark = pytest.mark.e2e


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
    ) -> bytes:
        headers = platform_admin.headers
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
        org_id = uuid.UUID(sol["organization_id"])

        # Declare each key + set each value in the source org.
        for position, (key, value) in enumerate(values.items()):
            schema_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{sol_id}/configs/{key}")
            db_session.add(
                SolutionConfigSchema(
                    id=schema_id,
                    solution_id=sol_id,
                    key=key,
                    type=config_type,
                    required=False,
                    description=f"Config {key} for import-secrets test",
                    default=None,
                    position=position,
                )
            )
            is_secret = config_type == ConfigTypeEnum.SECRET.value
            stored = encrypt_secret(str(value)) if is_secret else str(value)
            db_session.add(
                Config(
                    key=key,
                    value={"value": stored},
                    config_type=ConfigTypeEnum.SECRET if is_secret else ConfigTypeEnum.STRING,
                    organization_id=org_id,
                    updated_by="import-secrets-test",
                )
            )
        await db_session.commit()

        # Export via the real endpoint — exercises the real blob format.
        export_r = e2e_client.post(
            f"/api/solutions/{sol_id}/export?mode=full",
            json={"password": password},
            headers=headers,
        )
        assert export_r.status_code == 200, export_r.text
        zip_bytes = export_r.content
        assert len(zip_bytes) > 0, "export returned empty body"
        assert ".bifrost/secrets.enc" in zipfile.ZipFile(io.BytesIO(zip_bytes)).namelist(), (
            "full export must include .bifrost/secrets.enc"
        )
        return zip_bytes

    return _make


async def test_full_import_ignores_integration_owned_config_with_same_key(
    e2e_client, platform_admin, make_full_backup_zip, make_org, db_session
):
    """An integration-OWNED Config row (integration_id NOT NULL) sharing the key
    must NOT trigger a collision: solution config values live in the
    integration_id IS NULL partition, so the two rows never collide. The import
    must fill the solution's own NULL-partition slot and succeed (200)."""
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm.config import Config
    from src.models.orm.integrations import Integration

    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    zip_bytes = await make_full_backup_zip(values={"api_key": "xyz"}, password="pw")
    org = await make_org()

    # Seed an integration-owned config for the SAME key in the TARGET org. This
    # is the false-positive trap: a naive collision check (missing the
    # integration_id IS NULL filter) would 409 this valid import.
    integ = Integration(name=f"import-sec-integ-{uuid.uuid4().hex[:8]}")
    db_session.add(integ)
    await db_session.flush()
    db_session.add(
        Config(
            key="api_key",
            value={"value": "integration-owned-value"},
            config_type=ConfigTypeEnum.STRING,
            organization_id=org.id,
            integration_id=integ.id,
            updated_by="import-secrets-test-integ",
        )
    )
    await db_session.commit()

    r = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=upload_headers,
            files={"file": ("s.zip", zip_bytes, "application/zip")},
            data={"organization_id": str(org.id), "password": "pw"},
        ),
        headers,
    )
    assert r.status_code in (200, 201), r.text
    sol_id = r.json()["id"]

    setup_r = e2e_client.get(f"/api/solutions/{sol_id}/setup", headers=headers)
    assert setup_r.status_code == 200, setup_r.text
    api_key_item = next(
        (i for i in setup_r.json()["items"] if i["key"] == "api_key"), None
    )
    assert api_key_item is not None
    assert api_key_item["is_set"] is True, (
        "the solution's own (integration_id NULL) api_key slot must be filled "
        "despite the integration-owned row sharing the key"
    )


async def test_full_import_collision_refuses_without_replace_flag(
    e2e_client, platform_admin, make_full_backup_zip, make_org, db_session
):
    """An existing org config blocks import unless replace_secrets is set."""
    from sqlalchemy import select

    from src.core.security import decrypt_secret, encrypt_secret
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm.config import Config

    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)
    slug = f"import-sec-col-{uuid.uuid4().hex[:8]}"
    archive = await make_full_backup_zip(
        values={"api_key": "NEW"}, password="pw", slug=slug
    )
    org = await make_org()
    # A pre-existing workspace config is enough to exercise the collision.
    # Fresh-slot restoration is covered by the integration-owned-key E2E above.
    db_session.add(
        Config(
            key="api_key",
            value={"value": encrypt_secret("EXISTING")},
            config_type=ConfigTypeEnum.SECRET,
            organization_id=org.id,
            updated_by="import-secrets-collision-test",
        )
    )
    await db_session.commit()

    # The archive must name the colliding key when replacement is not allowed.
    r = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=upload_headers,
            files={"file": ("s.zip", archive, "application/zip")},
            data={"organization_id": str(org.id), "password": "pw"},
        ),
        headers,
    )
    assert r.status_code == 409, r.text
    assert "api_key" in r.text

    # The same archive replaces the value only with explicit consent.
    r2 = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=upload_headers,
            files={"file": ("s.zip", archive, "application/zip")},
            data={"organization_id": str(org.id), "password": "pw", "replace_secrets": "true"},
        ),
        headers,
    )
    assert r2.status_code in (200, 201), r2.text
    db_session.expire_all()
    stored = (
        await db_session.execute(
            select(Config).where(
                Config.key == "api_key",
                Config.organization_id == org.id,
                Config.integration_id.is_(None),
            )
        )
    ).scalar_one()
    assert decrypt_secret(stored.value["value"]) == "NEW"


async def test_wrong_password_rejected(
    e2e_client, platform_admin, make_full_backup_zip, make_org
):
    """A wrong password must be rejected with 422, and nothing must be created
    (decrypt-before-deploy contract: if decrypt fails, nothing lands at all)."""
    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    slug = f"import-sec-badpw-{uuid.uuid4().hex[:8]}"
    zip_bytes = await make_full_backup_zip(
        values={"api_key": "x"}, password="correct-pw", slug=slug
    )
    org = await make_org()

    # Wrong password → must fail (synchronous fail-fast 422; wait_for_install
    # passes the non-202 response through unchanged).
    r = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=upload_headers,
            files={"file": ("s.zip", zip_bytes, "application/zip")},
            data={"organization_id": str(org.id), "password": "WRONG"},
        ),
        headers,
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
