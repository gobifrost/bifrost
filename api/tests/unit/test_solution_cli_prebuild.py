"""Local app prebuilds for ``bifrost solution deploy``."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import UUID, uuid5

from bifrost.commands import solution as solution_command
from tests.unit.test_app_build_identity import RECIPE


INSTALL_ID = UUID("33333333-3333-3333-3333-333333333333")
APP_ID = "11111111-1111-1111-1111-111111111111"


def test_local_prebuild_ignores_stale_package_lock(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))

    monkeypatch.setattr(solution_command.subprocess, "run", fake_run)

    solution_command._run_local_vite_build(
        tmp_path,
        npm="/usr/bin/npm",
        npx="/usr/bin/npx",
        base="/apps/test/",
    )

    assert calls[0] == [
        "/usr/bin/npm",
        "install",
        "--no-audit",
        "--no-fund",
        "--package-lock=false",
    ]


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
            "bifrost": "file:./bifrost-sdk.tgz",
        }
        assert (workdir / "public/logo.png").read_bytes() == b"PNG-SOURCE"
        dist = workdir / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>built</html>")
        (dist / "asset.bin").write_bytes(b"\xff\x00")
        sdk_dir = workdir / "node_modules" / "bifrost"
        sdk_dir.mkdir(parents=True)
        (sdk_dir / "package.json").write_text(
            json.dumps(
                {
                    "version": "9.9.9",
                    "bifrost": {
                        "fingerprint": "sdk-built-by-cli",
                        "contract": 42,
                    },
                }
            )
        )

    monkeypatch.setattr(solution_command, "_run_local_vite_build", fake_build)

    built = solution_command._prebuild_apps(
        workspace,
        apps,
        install_id=INSTALL_ID,
        sdk_package=b"snapshot", recipe=RECIPE,
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
            "build_recipe": RECIPE,
            "prebuilt_sdk_metadata": {
                "package_version": "9.9.9",
                "fingerprint": "sdk-built-by-cli",
                "contract_version": 42,
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
        sdk_package=b"snapshot", recipe=RECIPE,
    )

    assert built == {}


def test_preflight_hit_skips_materialization_and_build(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("unchanged apps must not build")

    monkeypatch.setattr(solution_command, "_materialize_local_app", fail)
    monkeypatch.setattr(solution_command, "_run_local_vite_build", fail)
    monkeypatch.setattr(solution_command.shutil, "which", lambda name: f"/usr/bin/{name}")
    deployment = "44444444-4444-4444-4444-444444444444"
    result = solution_command._prebuild_apps(
        tmp_path, [{"id": APP_ID, "slug": "dashboard", "src_files": {"index.html": "hello"}}],
        install_id=INSTALL_ID, sdk_package=b"snapshot", recipe=RECIPE, reuse={APP_ID: deployment},
    )
    assert result[APP_ID]["reuse_deployment_id"] == deployment
    assert result[APP_ID]["dist_files"] == {}


def test_local_materialization_pins_downloaded_sdk(tmp_path):
    solution_command._materialize_local_app(tmp_path, {"src_files": {}}, sdk_package=b"snapshot")
    assert (tmp_path / "bifrost-sdk.tgz").read_bytes() == b"snapshot"
    assert json.loads((tmp_path / "package.json").read_text())["dependencies"]["bifrost"] == "file:./bifrost-sdk.tgz"


def test_reuse_overlay_survives_zip_app_collection(tmp_path):
    import yaml
    from bifrost.solution_definition import read_solution_apps

    manifest = tmp_path / ".bifrost" / "apps.yaml"
    manifest.parent.mkdir()
    manifest.write_text(yaml.safe_dump({"apps": {APP_ID: {"path": "apps/dash", "app_model": "standalone_v2"}}}))
    (tmp_path / "apps" / "dash").mkdir(parents=True)
    (tmp_path / "apps" / "dash" / "index.html").write_text("source")
    manifest.write_text(solution_command._apps_manifest_with_prebuilt_dist(tmp_path, {
        APP_ID: {"dist_files": {}, "bin_dist_files": {}, "build_recipe": {"schema": 1}, "reuse_deployment_id": "deployment"},
    }))
    definition = type("Definition", (), {"entries": lambda self, section: read_solution_apps(tmp_path)})()
    collected = solution_command._collect_apps(tmp_path, definition=definition)
    assert collected[0]["build_recipe"] == {"schema": 1}
    assert collected[0]["reuse_deployment_id"] == "deployment"
    assert collected[0]["src_files"]["index.html"] == "source"


async def test_cli_lookup_uses_install_scoped_input_digest(monkeypatch):
    import httpx
    from bifrost.app_build_identity import build_identity

    monkeypatch.setattr("bifrost.app_build_identity.build_recipe", lambda *a, **k: RECIPE)
    app = {"id": APP_ID, "slug": "dash", "src_files": {"index.html": "source"}, "dependencies": {"react": "18"}}
    fingerprint = build_identity(uuid5(INSTALL_ID, APP_ID), {"index.html": b"source"}, {"react": "18"}, RECIPE, package_name="dash")

    class Client:
        async def get(self, path):
            assert path == "/api/sdk/download"
            return httpx.Response(200, content=b"sdk-snapshot", request=httpx.Request("GET", "https://test/sdk"))

        async def post(self, path, *, json):
            assert path == f"/api/solutions/{INSTALL_ID}/app-builds/lookup"
            assert json == {"apps": [{"app_id": APP_ID, "fingerprint": fingerprint}]}
            return httpx.Response(200, json={"matches": {}}, request=httpx.Request("POST", "https://test/lookup"))

    assert await solution_command._lookup_local_builds(Client(), [app], str(INSTALL_ID)) == (b"sdk-snapshot", RECIPE, {})
