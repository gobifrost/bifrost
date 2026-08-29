import zipfile
from pathlib import Path

from bifrost.commands.app import _scaffold, _zip_project


def test_scaffold_is_a_vite_project_with_standard_ignored_env(tmp_path: Path) -> None:
    root = tmp_path / "my-app"
    _scaffold(root, "my-app")

    assert (root / "package.json").is_file()
    assert (root / "vite.config.ts").is_file()
    assert ".env" in (root / ".gitignore").read_text().splitlines()
    readme = (root / "README.md").read_text()
    assert "bifrost app start" in readme
    assert "standalone" not in readme.lower()
    app_source = (root / "src" / "App.tsx").read_text()
    assert "live Bifrost environment" in app_source
    assert "install's own workflow" not in app_source


def test_deploy_archive_never_contains_env_or_generated_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.tsx").write_text("export {}")
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "index.html").write_text("<div id='root'>")
    (tmp_path / ".env").write_text("SECRET=do-not-upload")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "index.js").write_text("generated")
    destination = tmp_path.parent / "source.zip"

    _zip_project(tmp_path, destination)

    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
    assert "src/main.tsx" in names
    assert ".env" not in names
    assert not any(name.startswith("node_modules/") for name in names)
