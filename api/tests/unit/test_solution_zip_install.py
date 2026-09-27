"""Tests for Solution zip-install validation and direct service contracts."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from src.services.solutions.zip_install import (
    BadExportPassword,
    PreviewResult,
    UnmetDependency,
    install_zip,
    preview_zip,
    validate_install_zip,
)


def test_safe_extract_rejects_compression_bomb(tmp_path: Path) -> None:
    from src.services.solutions.zip_install import _safe_extract

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("compressed.txt", b"0" * (2 * 1024 * 1024))

    with pytest.raises(ValueError, match="compression ratio"):
        _safe_extract(archive.getvalue(), str(tmp_path))


def _make_workspace_zip(extra: dict[str, str] | None = None) -> bytes:
    """Build an in-memory Solution workspace zip with a descriptor, a workflow
    manifest + source, and a required-secret config declaration."""
    files: dict[str, str] = {
        "bifrost.solution.yaml": (
            "slug: zip-demo\nname: Zip Demo\nscope: global\n"
        ),
        ".bifrost/workflows.yaml": (
            "workflows:\n"
            "  11111111-1111-1111-1111-111111111111:\n"
            "    id: 11111111-1111-1111-1111-111111111111\n"
            "    name: main\n"
            "    function_name: run\n"
            "    path: workflows/main.py\n"
        ),
        ".bifrost/configs.yaml": (
            "configs:\n"
            "  API_KEY:\n"
            "    id: API_KEY\n"
            "    key: API_KEY\n"
            "    type: secret\n"
            "    required: true\n"
            "    description: needed\n"
            "    position: 0\n"
        ),
        ".bifrost/files.yaml": (
            "locations:\n"
            "  - reports\n"
            "  - invoices\n"
        ),
        ".bifrost/claims.yaml": (
            "claims:\n"
            "  22222222-2222-2222-2222-222222222222:\n"
            "    id: 22222222-2222-2222-2222-222222222222\n"
            "    name: allowed_campus_ids\n"
            "    type: list\n"
            "    query:\n"
            "      table: memberships\n"
            "      select: campus_id\n"
        ),
        "workflows/main.py": "def run(sdk):\n    return 'ok'\n",
    }
    if extra:
        files.update(extra)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


@pytest.mark.e2e
async def test_install_zip_rejects_missing_module_before_persisting(db_session) -> None:
    """The installer invokes the dependency gate before committing an install."""
    from sqlalchemy import select

    from src.models.orm.solutions import Solution

    bundle = _make_workspace_zip(
        extra={
            "workflows/main.py": (
                "from modules.absent import x\n\n"
                "def run(sdk):\n    return x\n"
            )
        }
    )
    with pytest.raises(UnmetDependency, match="modules.absent"):
        await install_zip(
            db_session,
            bundle,
            organization_id=None,
            config_values={},
            deployer_email="dev@gobifrost.com",
        )

    await db_session.rollback()
    rows = (await db_session.execute(
        select(Solution).where(Solution.slug == "zip-demo")
    )).scalars().all()
    assert rows == []


def test_preview_lists_entities_and_config_schemas() -> None:
    result = preview_zip(_make_workspace_zip())
    assert isinstance(result, PreviewResult)
    assert result.slug == "zip-demo"
    assert result.name == "Zip Demo"

    assert len(result.workflows) == 1
    assert result.workflows[0]["name"] == "main"
    assert result.workflows[0]["function_name"] == "run"

    assert len(result.config_schemas) == 1
    decl = result.config_schemas[0]
    assert decl["key"] == "API_KEY"
    assert decl["type"] == "secret"
    assert decl["required"] is True

    assert len(result.claims) == 1
    assert result.claims[0]["name"] == "allowed_campus_ids"
    assert result.file_locations == ["reports", "invoices"]


def test_preview_empty_collections_when_absent() -> None:
    """A descriptor-only workspace previews with empty entity lists, not an error."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bifrost.solution.yaml", "slug: bare\nname: Bare\nscope: global\n")
    result = preview_zip(buf.getvalue())
    assert result.slug == "bare"
    assert result.workflows == []
    assert result.config_schemas == []
    assert result.apps == []


def test_zip_slip_member_is_rejected() -> None:
    """A member whose resolved path escapes the temp root must raise ValueError."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bifrost.solution.yaml", "slug: evil\nname: Evil\nscope: global\n")
        z.writestr("../evil.txt", "pwned")
    with pytest.raises(ValueError, match="unsafe path"):
        preview_zip(buf.getvalue())


def test_bad_zip_bytes_raise() -> None:
    """Non-zip bytes raise BadZipFile (the endpoint maps it to a 422)."""
    with pytest.raises(zipfile.BadZipFile):
        preview_zip(b"this is not a zip file")


def test_preview_requires_password_false_for_normal_zip() -> None:
    """A regular (shareable) zip without .bifrost/secrets.enc reports requires_password=False."""
    result = preview_zip(_make_workspace_zip())
    assert result.requires_password is False


def test_preview_requires_password_true_for_full_backup_zip() -> None:
    """A full-backup zip carrying .bifrost/secrets.enc reports requires_password=True."""
    result = preview_zip(
        _make_workspace_zip(extra={".bifrost/secrets.enc": "encrypted-blob-placeholder"})
    )
    assert result.requires_password is True


def _write_zip(tmp_path: Path, data: bytes) -> Path:
    zp = tmp_path / "solution.zip"
    zp.write_bytes(data)
    return zp


def test_validate_install_zip_accepts_normal_workspace(tmp_path: Path) -> None:
    """A well-formed shareable zip with no secrets blob passes fail-fast
    validation (no password needed) and returns the parsed preview so the
    endpoint can run its synchronous conflict checks (slug-keyed)."""
    zp = _write_zip(tmp_path, _make_workspace_zip())
    preview = validate_install_zip(zp, password=None)
    assert preview.slug == "zip-demo"
    assert preview.name == "Zip Demo"


def test_validate_install_zip_rejects_non_workspace(tmp_path: Path) -> None:
    """A zip missing the Solution descriptor slug/name is refused synchronously."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("random.txt", "not a workspace")
    zp = _write_zip(tmp_path, buf.getvalue())
    with pytest.raises(ValueError, match="not a Solution workspace"):
        validate_install_zip(zp, password=None)


def test_validate_install_zip_bad_bytes_raise(tmp_path: Path) -> None:
    """Corrupt (non-zip) bytes raise BadZipFile (endpoint maps to 422)."""
    zp = _write_zip(tmp_path, b"this is not a zip file")
    with pytest.raises(zipfile.BadZipFile):
        validate_install_zip(zp, password=None)


def test_validate_install_zip_missing_password_for_secrets(tmp_path: Path) -> None:
    """A full-backup zip carrying secrets.enc with NO password is refused
    synchronously (fail-fast, before any job)."""
    zp = _write_zip(
        tmp_path,
        _make_workspace_zip(extra={".bifrost/secrets.enc": "encrypted-blob"}),
    )
    with pytest.raises(BadExportPassword, match="password is required"):
        validate_install_zip(zp, password=None)


def test_validate_install_zip_wrong_password_for_secrets(tmp_path: Path) -> None:
    """A real secrets blob that fails to decrypt with the supplied password is
    refused synchronously with BadExportPassword (wrong password → 422, nothing
    lands)."""
    from src.services.solutions.secrets_blob import (
        SolutionContent,
        encode_secrets_blob,
    )

    blob = encode_secrets_blob(
        SolutionContent(config_values={"API_KEY": "sk_secret"}),
        password="correct-horse",
    )
    zp = _write_zip(
        tmp_path, _make_workspace_zip(extra={".bifrost/secrets.enc": blob})
    )
    with pytest.raises(BadExportPassword, match="wrong password"):
        validate_install_zip(zp, password="wrong-password")


def test_validate_install_zip_correct_password_for_secrets(tmp_path: Path) -> None:
    """The correct password decrypt-checks cleanly and validation passes."""
    from src.services.solutions.secrets_blob import (
        SolutionContent,
        encode_secrets_blob,
    )

    blob = encode_secrets_blob(
        SolutionContent(config_values={"API_KEY": "sk_secret"}),
        password="correct-horse",
    )
    zp = _write_zip(
        tmp_path, _make_workspace_zip(extra={".bifrost/secrets.enc": blob})
    )
    # Must not raise.
    validate_install_zip(zp, password="correct-horse")


@pytest.mark.e2e
async def test_secret_content_collision_requires_replace_and_preserves_integration_config(
    db_session,
) -> None:
    """Only solution-scope config rows collide, and replacement is explicit."""
    from sqlalchemy import select

    from src.core.security import decrypt_secret
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm.config import Config
    from src.models.orm.integrations import Integration
    from src.models.orm.organizations import Organization
    from src.models.orm.solution_config_schema import SolutionConfigSchema
    from src.models.orm.solutions import Solution
    from src.services.solutions.secrets_blob import SolutionContent
    from src.services.solutions.zip_install import (
        ContentCollision,
        _apply_content,
        _assert_no_unforced_collisions,
    )

    org = Organization(
        id=uuid4(), name=f"Import secrets {uuid4().hex[:8]}", created_by="test"
    )
    solution = Solution(
        id=uuid4(),
        slug=f"import-secrets-{uuid4().hex[:8]}",
        name="Import secrets",
        organization_id=org.id,
    )
    integration = Integration(name=f"Import secrets {uuid4().hex[:8]}")
    db_session.add_all([org, solution, integration])
    await db_session.flush()
    db_session.add_all(
        [
            SolutionConfigSchema(
                id=uuid4(),
                solution_id=solution.id,
                key="API_KEY",
                type=ConfigTypeEnum.SECRET.value,
            ),
            Config(
                key="API_KEY",
                value={"value": "integration-owned"},
                config_type=ConfigTypeEnum.STRING,
                organization_id=org.id,
                integration_id=integration.id,
                updated_by="test",
            ),
        ]
    )
    await db_session.flush()

    initial = SolutionContent(config_values={"API_KEY": "initial-secret"})
    await _assert_no_unforced_collisions(
        db_session,
        solution=solution,
        content=initial,
        replace_secrets=False,
        replace_data=False,
    )
    await _apply_content(
        db_session,
        solution=solution,
        content=initial,
        workspace=Path(),
        password=None,
        replace_secrets=False,
        replace_data=False,
        deployer_email="test",
    )

    replacement = SolutionContent(config_values={"API_KEY": "replacement-secret"})
    with pytest.raises(ContentCollision) as exc_info:
        await _assert_no_unforced_collisions(
            db_session,
            solution=solution,
            content=replacement,
            replace_secrets=False,
            replace_data=False,
        )
    assert exc_info.value.keys == ["API_KEY"]

    await _assert_no_unforced_collisions(
        db_session,
        solution=solution,
        content=replacement,
        replace_secrets=True,
        replace_data=False,
    )
    await _apply_content(
        db_session,
        solution=solution,
        content=replacement,
        workspace=Path(),
        password=None,
        replace_secrets=True,
        replace_data=False,
        deployer_email="test",
    )
    stored = (
        await db_session.execute(
            select(Config).where(
                Config.key == "API_KEY",
                Config.organization_id == org.id,
                Config.integration_id.is_(None),
            )
        )
    ).scalar_one()
    assert decrypt_secret(stored.value["value"]) == "replacement-secret"


@pytest.mark.e2e
async def test_table_content_collision_requires_replace_data_and_replaces_all_rows(
    db_session,
) -> None:
    """Existing runtime rows block import until replace_data replaces them all."""
    from sqlalchemy import select

    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution
    from src.models.orm.tables import Document, Table
    from src.services.solutions.secrets_blob import SolutionContent
    from src.services.solutions.zip_install import (
        ContentCollision,
        _apply_content,
        _assert_no_unforced_collisions,
    )

    org = Organization(
        id=uuid4(), name=f"Import table {uuid4().hex[:8]}", created_by="test"
    )
    solution = Solution(
        id=uuid4(),
        slug=f"import-table-{uuid4().hex[:8]}",
        name="Import table",
        organization_id=org.id,
    )
    table_name = f"import_rows_{uuid4().hex[:8]}"
    db_session.add_all([org, solution])
    await db_session.flush()

    table = Table(
        id=uuid4(),
        name=table_name,
        organization_id=org.id,
        solution_id=solution.id,
    )
    db_session.add(table)
    await db_session.flush()
    db_session.add(
        Document(
            id="target-only",
            table_id=table.id,
            data={"account": "south", "total": 99},
        )
    )
    await db_session.flush()

    exported = SolutionContent(
        table_data={table_name: [{"account": "north", "total": 42}]}
    )
    with pytest.raises(ContentCollision) as exc_info:
        await _assert_no_unforced_collisions(
            db_session,
            solution=solution,
            content=exported,
            replace_secrets=False,
            replace_data=False,
        )
    assert exc_info.value.keys == []
    assert exc_info.value.tables == [table_name]
    retained_rows = (
        (
            await db_session.execute(
                select(Document.data).where(Document.table_id == table.id)
            )
        )
        .scalars()
        .all()
    )
    assert retained_rows == [{"account": "south", "total": 99}]

    await _assert_no_unforced_collisions(
        db_session,
        solution=solution,
        content=exported,
        replace_secrets=False,
        replace_data=True,
    )
    await _apply_content(
        db_session,
        solution=solution,
        content=exported,
        workspace=Path(),
        password=None,
        replace_secrets=False,
        replace_data=True,
        deployer_email="test",
    )
    replaced_rows = (
        (
            await db_session.execute(
                select(Document.data).where(Document.table_id == table.id)
            )
        )
        .scalars()
        .all()
    )
    assert replaced_rows == [{"account": "north", "total": 42}]
