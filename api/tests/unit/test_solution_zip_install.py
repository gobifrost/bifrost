"""Unit tests for the Solution zip-install PREVIEW path (parse-only) + zip-slip
safety. The preview function unzips a Solution workspace, parses the manifests
via the CLI collectors, and returns what it would create — no DB, no S3, no
build. The COMMIT path is covered by the e2e test (it needs a live deployer)."""
from __future__ import annotations

import io
import zipfile

import pytest

from src.services.solutions.zip_install import PreviewResult, preview_zip


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
