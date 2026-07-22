"""Local app prebuilds for ``bifrost solution deploy``."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import UUID, uuid5

from bifrost.commands import solution as solution_command


INSTALL_ID = UUID("33333333-3333-3333-3333-333333333333")
APP_ID = "11111111-1111-1111-1111-111111111111"


def test_prebuild_apps_builds_in_temp_with_install_scoped_base(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    apps = [
        {
            "id": APP_ID,
            "slug": "dashboard",
            "name": "Dashboard",
            "app_model": "standalone_v2",
            "dependencies": {"react": "^18.0.0"},
            "src_files": {
                "index.html": "<div id='root'></div>",
                "package.json": json.dumps(
                    {
                        "name": "dashboard",
                        "dependencies": {"react-dom": "^18.0.0"},
                    }
                ),
                "src/main.tsx": "export default 1\n",
            },
            "bin_files": {
                "public/logo.png": base64.b64encode(b"PNG-SOURCE").decode("ascii")
            },
            "dist_files": None,
            "bin_dist_files": None,
        }
    ]
    observed: dict[str, object] = {}

    monkeypatch.setattr(solution_command.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_build(workdir: Path, *, npm: str, npx: str, base: str) -> None:
        observed.update(workdir=workdir, npm=npm, npx=npx, base=base)
        package = json.loads((workdir / "package.json").read_text())
        assert package["dependencies"] == {
            "react-dom": "^18.0.0",
            "react": "^18.0.0",
            "bifrost": "https://bifrost.example/api/sdk/download",
        }
        assert (workdir / "public/logo.png").read_bytes() == b"PNG-SOURCE"
        dist = workdir / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>built</html>")
        (dist / "asset.bin").write_bytes(b"\xff\x00")

    monkeypatch.setattr(solution_command, "_run_local_vite_build", fake_build)

    built = solution_command._prebuild_apps(
        workspace,
        apps,
        install_id=INSTALL_ID,
        api_url="https://bifrost.example",
    )

    deployed_id = uuid5(INSTALL_ID, APP_ID)
    assert observed["base"] == f"/api/applications/{deployed_id}/dist/"
    assert observed["npm"] == "/usr/bin/npm"
    assert observed["npx"] == "/usr/bin/npx"
    assert built == {
        APP_ID: {
            "dist_files": {"index.html": "<html>built</html>"},
            "bin_dist_files": {
                "asset.bin": base64.b64encode(b"\xff\x00").decode("ascii")
            },
        }
    }
    assert not Path(observed["workdir"]).exists()


def test_prebuild_apps_preserves_manifest_prebuilt_only_app(tmp_path: Path) -> None:
    app = {
        "id": APP_ID,
        "slug": "dashboard",
        "name": "Dashboard",
        "app_model": "standalone_v2",
        "dependencies": {},
        "src_files": {},
        "bin_files": {},
        "dist_files": {"index.html": "already built"},
        "bin_dist_files": None,
    }

    built = solution_command._prebuild_apps(
        tmp_path,
        [app],
        install_id=INSTALL_ID,
        api_url="https://bifrost.example",
    )

    assert built == {}
