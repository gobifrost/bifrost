from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.models.orm.agents import Agent
from src.services.agent_skills import (
    build_agent_skill_archive,
    render_skill_markdown,
    skill_slug,
)


def _agent(**overrides) -> Agent:
    values = {
        "name": "Ticket Triage!",
        "description": "Triage incoming support tickets",
        "system_prompt": "Follow the triage runbook.",
        "bundle_path": None,
        "created_by": "admin@example.test",
    }
    values.update(overrides)
    return Agent(**values)


def test_skill_projection_uses_portable_frontmatter() -> None:
    rendered = render_skill_markdown(_agent())

    assert skill_slug("Ticket Triage!") == "ticket-triage"
    assert "name: ticket-triage" in rendered
    assert 'description: "Triage incoming support tickets"' in rendered
    assert rendered.endswith("Follow the triage runbook.\n")


@pytest.mark.asyncio
async def test_inline_agent_exports_a_conventional_skill_zip() -> None:
    async with build_agent_skill_archive(_agent()) as archive_path:
        assert archive_path.is_file()
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.namelist() == ["ticket-triage/SKILL.md"]
            skill = archive.read("ticket-triage/SKILL.md").decode()

    assert "Follow the triage runbook." in skill
    assert not archive_path.exists()


@pytest.mark.asyncio
async def test_bundle_export_keeps_companion_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Storage:
        async def read(self, path: str) -> bytes:
            if path == "agents/triage/SKILL.md":
                return (
                    b"---\nname: ticket-triage\n"
                    b"description: Triage tickets\n---\n\nCanonical instructions\n"
                )
            assert path == "agents/triage/references/runbook.md"
            return b"# Runbook\n"

    async def bundle_files(_agent: Agent):
        return Storage(), [
            ("references/runbook.md", "agents/triage/references/runbook.md")
        ]

    monkeypatch.setattr(
        "src.services.agent_skills._bundle_files",
        bundle_files,
    )
    monkeypatch.setattr(
        "src.services.agent_skills._bundle_storage",
        lambda _agent: Storage(),
    )

    async with build_agent_skill_archive(
        _agent(bundle_path="agents/triage")
    ) as archive_path:
        with zipfile.ZipFile(Path(archive_path)) as archive:
            assert archive.namelist() == [
                "ticket-triage/SKILL.md",
                "ticket-triage/references/runbook.md",
            ]
            assert (
                archive.read("ticket-triage/references/runbook.md")
                == b"# Runbook\n"
            )
            assert b"Canonical instructions" in archive.read(
                "ticket-triage/SKILL.md"
            )
