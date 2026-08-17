"""Builder workspace validation must agree with Solution deploy."""

import json
from pathlib import Path

import yaml

from src.services.builder.scaffold import validate_workspace


def _workspace(tmp_path: Path, app_fields: dict[str, object]) -> Path:
    (tmp_path / "bifrost.solution.yaml").write_text(
        "slug: validation\nname: Validation\n",
        encoding="utf-8",
    )
    app_path = tmp_path / app_fields["path"]
    app_path.mkdir(parents=True)
    manifest = tmp_path / ".bifrost" / "apps.yaml"
    manifest.parent.mkdir()
    manifest.write_text(
        yaml.safe_dump({"apps": {app_fields["id"]: app_fields}}),
        encoding="utf-8",
    )
    return tmp_path


def test_builder_accepts_standalone_v2_app_manifest(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "slug": "portal",
            "name": "Portal",
            "path": "apps/portal",
            "app_model": "standalone_v2",
        },
    )

    assert validate_workspace(workspace) == []


def test_builder_rejects_legacy_or_misnamed_app_model(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "slug": "portal",
            "name": "Portal",
            "path": "apps/portal",
            "type": "standalone_v2",
        },
    )

    assert validate_workspace(workspace) == [
        "app 'portal' must set app_model: standalone_v2 in "
        ".bifrost/apps.yaml; found 'inline_v1'"
    ]


def test_builder_rejects_dependencies_that_deploy_would_refuse(
    tmp_path: Path,
) -> None:
    workspace = _workspace(
        tmp_path,
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "slug": "portal",
            "name": "Portal",
            "path": "apps/portal",
            "app_model": "standalone_v2",
        },
    )
    (workspace / "apps" / "portal" / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"react": "^18.3.1"},
                "devDependencies": {"autoprefixer": "^10.4.19"},
            }
        ),
        encoding="utf-8",
    )

    assert validate_workspace(workspace) == [
        "app 'portal' build input is invalid: Unsupported build dependencies: "
        "autoprefixer@^10.4.19 (not in the curated catalog), "
        "react@^18.3.1 (use 18.2.0)"
    ]
