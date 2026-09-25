"""E2E: full-backup zip import restores an encrypted secret through a job.

Task 13 of the Solutions success-criteria programme.

Contract under test: a full-backup zip (with .bifrost/secrets.enc) is exported,
queued for install, and restores its secret value at rest in the target organization.

Collision and replacement transitions are covered directly by the zip-install
service tests. Wrong-password rejection is checked at the HTTP boundary below
and by the synchronous ``validate_install_zip`` unit test.

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


async def test_full_import_restores_encrypted_secret_via_platform_job(
    e2e_client, platform_admin, make_full_backup_zip, make_org, db_session
):
    """A real encrypted export restores its plaintext only through the job."""
    from sqlalchemy import select

    from src.core.security import decrypt_secret
    from src.models.orm.config import Config

    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    zip_bytes = await make_full_backup_zip(values={"api_key": "xyz"}, password="pw")
    org = await make_org()

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
    assert decrypt_secret(stored.value["value"]) == "xyz"


async def test_wrong_password_rejected_before_install(
    e2e_client, platform_admin, make_full_backup_zip, make_org
):
    """A bad password returns 422 without creating the target install."""
    headers = platform_admin.headers
    slug = f"import-sec-badpw-{uuid.uuid4().hex[:8]}"
    archive = await make_full_backup_zip(
        values={"api_key": "x"}, password="correct-pw", slug=slug
    )
    org = await make_org()

    response = e2e_client.post(
        "/api/solutions/install",
        headers=_upload_headers(headers),
        files={"file": ("s.zip", archive, "application/zip")},
        data={"organization_id": str(org.id), "password": "WRONG"},
    )
    assert response.status_code == 422, response.text

    listed = e2e_client.get("/api/solutions", headers=headers)
    assert listed.status_code == 200, listed.text
    assert not any(
        item["slug"] == slug and item.get("organization_id") == str(org.id)
        for item in listed.json()["solutions"]
    )
