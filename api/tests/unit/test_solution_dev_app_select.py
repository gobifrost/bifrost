from pathlib import Path

import pytest
import yaml

from bifrost.solution_dev.app_select import AppSelectionError, select_app


def _apps_yaml(tmp_path: Path, apps: dict) -> None:
    (tmp_path / ".bifrost").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".bifrost" / "apps.yaml").write_text(yaml.safe_dump({"apps": apps}))


def test_sole_standalone_v2_app_auto_selected(tmp_path: Path):
    _apps_yaml(tmp_path, {"u1": {"id": "u1", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"}})
    chosen = select_app(tmp_path, slug=None)
    assert chosen.app_id == "u1"
    assert chosen.slug == "dash"
    assert chosen.app_dir == tmp_path / "apps/dash"


def test_explicit_slug_selects_it(tmp_path: Path):
    _apps_yaml(tmp_path, {
        "u1": {"id": "u1", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
        "u2": {"id": "u2", "slug": "admin", "path": "apps/admin", "app_model": "standalone_v2"},
    })
    chosen = select_app(tmp_path, slug="admin")
    assert chosen.app_id == "u2"


def test_multiple_without_slug_errors_and_lists(tmp_path: Path):
    _apps_yaml(tmp_path, {
        "u1": {"id": "u1", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"},
        "u2": {"id": "u2", "slug": "admin", "path": "apps/admin", "app_model": "standalone_v2"},
    })
    with pytest.raises(AppSelectionError) as e:
        select_app(tmp_path, slug=None)
    assert "dash" in str(e.value) and "admin" in str(e.value)


def test_no_v2_apps_errors_with_scaffold_hint(tmp_path: Path):
    _apps_yaml(tmp_path, {})
    with pytest.raises(AppSelectionError) as e:
        select_app(tmp_path, slug=None)
    assert "scaffold-app" in str(e.value)


def test_unknown_slug_errors(tmp_path: Path):
    _apps_yaml(tmp_path, {"u1": {"id": "u1", "slug": "dash", "path": "apps/dash", "app_model": "standalone_v2"}})
    with pytest.raises(AppSelectionError):
        select_app(tmp_path, slug="nope")
