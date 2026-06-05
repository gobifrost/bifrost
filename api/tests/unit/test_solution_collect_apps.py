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


def test_collect_apps_empty_when_no_manifest(tmp_path) -> None:
    assert _collect_apps(tmp_path) == []
