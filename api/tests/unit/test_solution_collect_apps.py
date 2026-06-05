"""CLI `_collect_apps` — reads .bifrost/apps.yaml (keyed by UUID) + app source
into the deploy bundle (v2 Task 5)."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.commands.solution import _collect_apps  # noqa: E402


def _ws(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / "apps" / "dash").mkdir(parents=True)
    (tmp_path / "apps" / "dash" / "index.html").write_text("<html></html>")
    (tmp_path / "apps" / "dash" / "src").mkdir()
    (tmp_path / "apps" / "dash" / "src" / "main.tsx").write_text("export default 1\n")
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        "apps:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    id: 11111111-1111-1111-1111-111111111111\n"
        "    slug: dash\n"
        "    name: Dashboard\n"
        "    path: apps/dash\n"
        "    app_model: standalone_v2\n"
        "    dependencies: {react: ^18.0.0}\n"
    )
    return tmp_path


def test_collect_apps_reads_manifest_and_source(tmp_path) -> None:
    apps = _collect_apps(_ws(tmp_path))
    assert len(apps) == 1
    a = apps[0]
    assert a["id"] == "11111111-1111-1111-1111-111111111111"
    assert a["slug"] == "dash"
    assert a["name"] == "Dashboard"            # name from body, not the UUID key
    assert a["app_model"] == "standalone_v2"
    assert a["dependencies"] == {"react": "^18.0.0"}
    # Source files read relative to the app dir (build input).
    assert a["src_files"]["index.html"] == "<html></html>"
    assert a["src_files"]["src/main.tsx"] == "export default 1\n"


def test_collect_apps_carries_role_bindings(tmp_path) -> None:
    """Role refs must reach the bundle so the deployer can sync AppRole (P1-d)."""
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / "apps" / "dash").mkdir(parents=True)
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        "apps:\n"
        "  22222222-2222-2222-2222-222222222222:\n"
        "    id: 22222222-2222-2222-2222-222222222222\n"
        "    slug: dash\n"
        "    name: Dashboard\n"
        "    path: apps/dash\n"
        "    app_model: inline_v1\n"
        "    access_level: role_based\n"
        "    role_names: [Support]\n"
    )
    apps = _collect_apps(tmp_path)
    assert apps[0]["access_level"] == "role_based"
    assert apps[0]["role_names"] == ["Support"]


def test_collect_apps_carries_binary_assets_as_base64(tmp_path) -> None:
    """Non-text assets (png/fonts/public/) must be carried as base64 in
    bin_files, not silently dropped (Codex P2-j/R4)."""
    import base64

    (tmp_path / ".bifrost").mkdir()
    app = tmp_path / "apps" / "dash"
    (app / "public").mkdir(parents=True)
    (app / "src").mkdir()
    (app / "src" / "main.tsx").write_text("import './logo.png'\n")
    png_bytes = b"\x89PNG\r\n\x1a\n\x00BINARY"
    (app / "logo.png").write_bytes(png_bytes)
    (app / "public" / "font.woff2").write_bytes(b"WOFF2DATA")
    (app / ".DS_Store").write_bytes(b"junk")  # must be skipped
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        "apps:\n"
        "  33333333-3333-3333-3333-333333333333:\n"
        "    id: 33333333-3333-3333-3333-333333333333\n"
        "    slug: dash\n    name: Dashboard\n    path: apps/dash\n"
        "    app_model: standalone_v2\n"
    )
    apps = _collect_apps(tmp_path)
    a = apps[0]
    # text stays in src_files
    assert a["src_files"]["src/main.tsx"] == "import './logo.png'\n"
    # binary assets carried as base64 in bin_files, round-trippable
    assert base64.b64decode(a["bin_files"]["logo.png"]) == png_bytes
    assert base64.b64decode(a["bin_files"]["public/font.woff2"]) == b"WOFF2DATA"
    # OS cruft skipped
    assert ".DS_Store" not in a["bin_files"]


def test_collect_apps_skips_node_modules_and_dist(tmp_path) -> None:
    """After a dev's `npm install`/`npm run dev`, the app dir holds node_modules/
    dist/etc; those must NOT be bundled — only real source (Codex R5)."""
    (tmp_path / ".bifrost").mkdir()
    app = tmp_path / "apps" / "dash"
    (app / "src").mkdir(parents=True)
    (app / "src" / "main.tsx").write_text("export {}\n")
    # Generated/dependency junk that must be skipped.
    (app / "node_modules" / "react" / "dist").mkdir(parents=True)
    (app / "node_modules" / "react" / "dist" / "index.js").write_text("/*react*/")
    (app / "node_modules" / "logo.png").write_bytes(b"\x89PNGdep")
    (app / "dist").mkdir()
    (app / "dist" / "index.html").write_text("<built/>")
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        "apps:\n  44444444-4444-4444-4444-444444444444:\n"
        "    id: 44444444-4444-4444-4444-444444444444\n"
        "    slug: dash\n    name: D\n    path: apps/dash\n    app_model: standalone_v2\n"
    )
    a = _collect_apps(tmp_path)[0]
    # Real source collected.
    assert a["src_files"]["src/main.tsx"] == "export {}\n"
    # Nothing from node_modules/ or dist/ — neither text nor binary.
    all_keys = list(a["src_files"]) + list(a["bin_files"])
    assert not any(k.startswith("node_modules/") for k in all_keys)
    assert not any(k.startswith("dist/") for k in all_keys)


def test_collect_apps_excludes_env_files(tmp_path) -> None:
    """Local env files (.env / .env.local) must NEVER be bundled — they hold the
    dev's BIFROST_ACCESS_TOKEN, and shipping one lets the server-side Vite build
    bake the token into public JS, leaking it to every app user (Codex R6-P1-c).
    The token reaches a deployed app via window.__BIFROST_APP__, not the bundle."""
    (tmp_path / ".bifrost").mkdir()
    app = tmp_path / "apps" / "dash"
    (app / "src").mkdir(parents=True)
    (app / "src" / "main.tsx").write_text("export {}\n")
    (app / ".env").write_text("BIFROST_ACCESS_TOKEN=secret-token\n")
    (app / ".env.local").write_text("BIFROST_ACCESS_TOKEN=secret-too\n")
    (app / ".env.production").write_text("BIFROST_API_URL=https://x\n")
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        "apps:\n  55555555-5555-5555-5555-555555555555:\n"
        "    id: 55555555-5555-5555-5555-555555555555\n"
        "    slug: dash\n    name: D\n    path: apps/dash\n    app_model: standalone_v2\n"
    )
    a = _collect_apps(tmp_path)[0]
    assert a["src_files"]["src/main.tsx"] == "export {}\n"
    all_keys = list(a["src_files"]) + list(a["bin_files"])
    assert not any(k == ".env" or k.startswith(".env.") for k in all_keys), all_keys


def test_collect_apps_empty_when_no_manifest(tmp_path) -> None:
    assert _collect_apps(tmp_path) == []
