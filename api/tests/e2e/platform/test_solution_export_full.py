"""E2E: GET /api/solutions/{id}/export?mode=full — encrypted secrets blob.

Verifies that:
- ``mode=full`` without a password returns 422.
- ``mode=full&password=pw`` returns a zip with ``.bifrost/secrets.enc``.
- The blob decrypts correctly and carries the config value.
- The default (shareable) export does NOT include ``.bifrost/secrets.enc``.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def make_solution_with_set_config(e2e_client, platform_admin, db_session):
    """Factory: create a Solution with a declared config AND a set value.

    Returns a coroutine that accepts ``key`` and ``value`` kwargs and returns
    a simple namespace with ``.id`` and ``.organization_id``.

    The config is declared as STRING type and the value is set directly in the
    DB using Config's JSONB ``{"value": ...}`` storage shape (same as
    set_config does).  This avoids needing to go through the full set_config
    HTTP path while still matching the exact storage format that _config_values
    must read.
    """
    from types import SimpleNamespace

    from src.core.security import encrypt_secret
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm.config import Config
    from src.models.orm.solution_config_schema import SolutionConfigSchema

    async def _make(
        key: str = "api_key", value: str = "xyz", config_type: str = "string"
    ) -> SimpleNamespace:
        headers = platform_admin.headers
        slug = f"export-full-{uuid.uuid4().hex[:8]}"
        r = e2e_client.post(
            "/api/solutions",
            headers=headers,
            json={"slug": slug, "name": slug.upper(), "scope": "org"},
        )
        assert r.status_code in (200, 201), r.text
        sol = r.json()
        sol_id = uuid.UUID(sol["id"])
        org_id = uuid.UUID(sol["organization_id"]) if sol.get("organization_id") else None

        # Declare the config schema on the solution.
        decl = SolutionConfigSchema(
            solution_id=sol_id,
            key=key,
            type=config_type,
            required=False,
            description="Config for full-export test",
            default=None,
            position=0,
        )
        db_session.add(decl)

        # Set the config value in the solution's org scope — exact storage shape
        # that set_config (and _config_values) uses: JSONB {"value": ...}. For a
        # SECRET config the stored value is encrypted-at-rest (encrypt_secret),
        # exactly as set_config does, so the export path must decrypt it back to
        # plaintext before it lands in the blob.
        is_secret = config_type == ConfigTypeEnum.SECRET.value
        stored = encrypt_secret(value) if is_secret else value
        db_session.add(
            Config(
                key=key,
                value={"value": stored},
                config_type=ConfigTypeEnum.SECRET if is_secret else ConfigTypeEnum.STRING,
                organization_id=org_id,
                updated_by="export-full-test",
            )
        )
        await db_session.commit()

        return SimpleNamespace(id=str(sol_id), organization_id=org_id)

    return _make


async def test_full_export_includes_encrypted_secrets_blob(
    e2e_client, platform_admin, make_solution_with_set_config
):
    sol = await make_solution_with_set_config(key="api_key", value="xyz")
    headers = platform_admin.headers

    # mode=full without a password must be rejected.
    bad = e2e_client.get(f"/api/solutions/{sol.id}/export?mode=full", headers=headers)
    assert bad.status_code == 422

    # mode=full with a password must return a zip containing secrets.enc.
    ok = e2e_client.get(
        f"/api/solutions/{sol.id}/export?mode=full&password=pw", headers=headers
    )
    assert ok.status_code == 200, ok.text
    names = zipfile.ZipFile(io.BytesIO(ok.content)).namelist()
    assert ".bifrost/secrets.enc" in names

    # The blob must decrypt and carry the value.
    from src.services.solutions.secrets_blob import decode_secrets_blob

    blob = zipfile.ZipFile(io.BytesIO(ok.content)).read(".bifrost/secrets.enc").decode()
    content = decode_secrets_blob(blob, password="pw")
    assert content.config_values.get("api_key") == "xyz"

    # Shareable export (default) must NOT include the blob.
    sh = e2e_client.get(f"/api/solutions/{sol.id}/export", headers=headers)
    assert sh.status_code == 200, sh.text
    sh_names = zipfile.ZipFile(io.BytesIO(sh.content)).namelist()
    assert ".bifrost/secrets.enc" not in sh_names


async def test_full_export_decrypts_secret_typed_config(
    e2e_client, platform_admin, make_solution_with_set_config
):
    """Security-critical path: a SECRET-typed config is stored encrypted-at-rest
    (encrypt_secret); the full export must decrypt it so the blob carries the
    PLAINTEXT, not the ciphertext.

    This fails loudly if the is_secret/decrypt gate in _config_values regresses —
    a still-encrypted export would make ``== "my-secret"`` compare plaintext
    against ciphertext.
    """
    sol = await make_solution_with_set_config(
        key="db_password", value="my-secret", config_type="secret"
    )
    headers = platform_admin.headers

    ok = e2e_client.get(
        f"/api/solutions/{sol.id}/export?mode=full&password=pw", headers=headers
    )
    assert ok.status_code == 200, ok.text

    from src.services.solutions.secrets_blob import decode_secrets_blob

    blob = zipfile.ZipFile(io.BytesIO(ok.content)).read(".bifrost/secrets.enc").decode()
    content = decode_secrets_blob(blob, password="pw")
    # Came out DECRYPTED — proves _config_values decrypted the at-rest ciphertext
    # before placing it in the password blob.
    assert content.config_values["db_password"] == "my-secret"
