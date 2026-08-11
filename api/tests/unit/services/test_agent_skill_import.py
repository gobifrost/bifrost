from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.services.agent_skill_import import (
    import_agent_skill_archive,
    skill_instruction_body,
)
from src.services.builder.fs_tools import WorkspaceViolation


def _archive(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return path


def test_import_normalizes_one_wrapper_and_uses_frontmatter_name(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "expenses.skill",
        {
            "expense-bundle/SKILL.md": (
                b"---\nname: Expense Tracker\n"
                b"description: Track company expenses\n---\n\nFollow the policy.\n"
            ),
            "expense-bundle/references/policy.md": b"# Policy\n",
        },
    )

    imported = import_agent_skill_archive(archive)

    assert imported.bundle_path == "skills/expense-tracker"
    assert imported.name == "Expense Tracker"
    assert imported.description == "Track company expenses"
    assert imported.skill_markdown.endswith("Follow the policy.\n")
    assert set(imported.files) == {
        "skills/expense-tracker/SKILL.md",
        "skills/expense-tracker/references/policy.md",
    }


def test_import_rejects_files_outside_portable_skill_roots(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "bad.zip",
        {
            "SKILL.md": (
                b"---\nname: bad\ndescription: Bad bundle\n---\n\nInstructions\n"
            ),
            "secrets.env": b"nope",
        },
    )

    with pytest.raises(WorkspaceViolation, match="assets/"):
        import_agent_skill_archive(archive)


def test_import_requires_skill_frontmatter(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "bad.zip",
        {"SKILL.md": b"# Missing frontmatter\n"},
    )

    with pytest.raises(WorkspaceViolation, match="frontmatter"):
        import_agent_skill_archive(archive)


def test_detach_preserves_only_instruction_body() -> None:
    markdown = (
        "---\nname: helper\ndescription: Help\nmetadata: true\n---\n\n"
        "# Instructions\n\nDo the work.\n"
    )

    assert skill_instruction_body(markdown) == "# Instructions\n\nDo the work."
