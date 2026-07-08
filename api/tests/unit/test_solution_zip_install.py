"""Unit tests for the Solution zip-install PREVIEW path (parse-only) + zip-slip
safety. The preview function unzips a Solution workspace, parses the manifests
via the CLI collectors, and returns what it would create — no DB, no S3, no
build. The COMMIT path is covered by the e2e test (it needs a live deployer)."""
from __future__ import annotations

import io
import zipfile

import pytest

from pathlib import Path

from src.services.solutions.zip_install import (
    BadExportPassword,
    PreviewResult,
    preview_zip,
    validate_install_zip,
)


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
