from pathlib import Path

from src.services.legacy_entity_files import (
    is_legacy_entity_file,
    remove_legacy_entity_files,
)


def test_legacy_entity_file_requires_direct_uuid_named_companion() -> None:
    assert is_legacy_entity_file(
        "forms/11111111-1111-1111-1111-111111111111.form.yaml"
    )
    assert is_legacy_entity_file(
        "agents/22222222-2222-2222-2222-222222222222.agent.yaml"
    )
    assert not is_legacy_entity_file("forms/runbook.form.yaml")
    assert not is_legacy_entity_file(
        "forms/archive/11111111-1111-1111-1111-111111111111.form.yaml"
    )


def test_remove_legacy_entity_files_preserves_ordinary_yaml(tmp_path: Path) -> None:
    legacy = tmp_path / "forms/11111111-1111-1111-1111-111111111111.form.yaml"
    ordinary = tmp_path / "forms/runbook.form.yaml"
    legacy.parent.mkdir()
    legacy.write_text("legacy")
    ordinary.write_text("ordinary")

    assert remove_legacy_entity_files(tmp_path) == [
        "forms/11111111-1111-1111-1111-111111111111.form.yaml"
    ]
    assert not legacy.exists()
    assert ordinary.read_text() == "ordinary"


def test_remove_legacy_entity_files_does_not_follow_directory_symlink(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "11111111-1111-1111-1111-111111111111.form.yaml"
    target.write_text("outside")
    work_dir = tmp_path / "checkout"
    work_dir.mkdir()
    (work_dir / "forms").symlink_to(outside, target_is_directory=True)

    assert remove_legacy_entity_files(work_dir) == []
    assert target.read_text() == "outside"


def test_remove_legacy_entity_files_unlinks_file_symlink_only(tmp_path: Path) -> None:
    outside = tmp_path / "outside.form.yaml"
    outside.write_text("outside")
    work_dir = tmp_path / "checkout"
    forms = work_dir / "forms"
    forms.mkdir(parents=True)
    link = forms / "11111111-1111-1111-1111-111111111111.form.yaml"
    link.symlink_to(outside)

    assert remove_legacy_entity_files(work_dir) == [
        "forms/11111111-1111-1111-1111-111111111111.form.yaml"
    ]
    assert not link.exists()
    assert outside.read_text() == "outside"
