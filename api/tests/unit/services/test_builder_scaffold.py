"""Builder workspace validation must agree with Solution deploy."""

import json
from pathlib import Path
from uuid import uuid4, uuid5

import yaml

from src.services.builder.scaffold import (
    build_initial_workspace,
    strip_legacy_builder_assets,
    validate_workspace,
)


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


def test_builder_initial_workspace_is_skill_bundle_free(tmp_path: Path) -> None:
    build_initial_workspace(
        tmp_path,
        slug="hello",
        name="Hello",
    )

    assert (tmp_path / "bifrost.solution.yaml").is_file()
    assert (tmp_path / "README.md").is_file()
    assert (tmp_path / "workflows" / ".gitkeep").is_file()
    assert not (tmp_path / "skills" / "bifrost-build").exists()
    assert not (tmp_path / ".bifrost" / "agents.yaml").exists()


def test_legacy_builder_assets_are_removed_without_touching_other_agents(
    tmp_path: Path,
) -> None:
    solution_id = uuid4()
    legacy_id = str(
        uuid5(solution_id, "bifrost-private-solution-builder-agent")
    )
    other_id = str(uuid4())
    skill = tmp_path / "skills" / "bifrost-build"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("legacy", encoding="utf-8")
    manifest = tmp_path / ".bifrost" / "agents.yaml"
    manifest.parent.mkdir()
    manifest.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    legacy_id: {"id": legacy_id, "name": "Builder"},
                    other_id: {"id": other_id, "name": "Keep Me"},
                }
            }
        ),
        encoding="utf-8",
    )

    strip_legacy_builder_assets(tmp_path, solution_id=solution_id)

    assert not (tmp_path / "skills").exists()
    remaining = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert list(remaining["agents"]) == [other_id]
