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


def test_collect_apps_empty_when_no_manifest(tmp_path) -> None:
    assert _collect_apps(tmp_path) == []
