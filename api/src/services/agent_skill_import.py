"""Validation and normalization for uploaded Agent Skill archives."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from src.services.agent_skills import parse_skill_frontmatter, skill_slug
from src.services.builder.fs_tools import (
    MIB,
    WorkspaceLimits,
    WorkspaceViolation,
    safe_extract_zip,
)

AGENT_SKILL_ARCHIVE_LIMIT = 25 * MIB
AGENT_SKILL_LIMITS = WorkspaceLimits(
    max_files=500,
    max_file_bytes=5 * MIB,
    max_total_bytes=25 * MIB,
)
_ALLOWED_COMPANION_ROOTS = frozenset({"assets", "references", "scripts"})


@dataclass(frozen=True)
class ImportedAgentSkill:
    """A validated archive expressed as canonical agent-scoped storage keys."""

    name: str
    description: str
    bundle_path: str
    skill_markdown: str
    files: dict[str, bytes]


def skill_instruction_body(markdown: str) -> str:
    """Strip portable frontmatter when turning a detached Skill back inline."""
    normalized = markdown.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return markdown.strip()
    closing = normalized.find("\n---\n", 4)
    if closing < 0:
        return markdown.strip()
    return normalized[closing + 5 :].strip()


def import_agent_skill_archive(archive_path: Path) -> ImportedAgentSkill:
    """Validate a ``.zip``/``.skill`` archive and return normalized files.

    The archive may contain files at its root or under one wrapper directory.
    Only the Agent Skills companion directories are accepted. This keeps the
    upload portable and prevents a Skill from becoming a general-purpose object
    storage write primitive.
    """
    with tempfile.TemporaryDirectory(prefix="bifrost-agent-skill-import-") as tmp:
        root = Path(tmp)
        extracted = safe_extract_zip(archive_path, root, AGENT_SKILL_LIMITS)
        skill_candidates = [
            PurePosixPath(path)
            for path in extracted
            if PurePosixPath(path).name == "SKILL.md"
        ]
        if len(skill_candidates) != 1:
            raise WorkspaceViolation(
                "archive must contain exactly one SKILL.md at its root or in one wrapper directory"
            )

        skill_path = skill_candidates[0]
        if len(skill_path.parts) == 1:
            wrapper: tuple[str, ...] = ()
        elif len(skill_path.parts) == 2:
            wrapper = (skill_path.parts[0],)
        else:
            raise WorkspaceViolation(
                "SKILL.md must be at the archive root or inside one wrapper directory"
            )

        relative_files: dict[str, bytes] = {}
        for raw_path in extracted:
            pure = PurePosixPath(raw_path)
            if wrapper:
                if pure.parts[:1] != wrapper:
                    raise WorkspaceViolation(
                        "archive contains files outside the Skill wrapper directory"
                    )
                pure = PurePosixPath(*pure.parts[1:])
            if not pure.parts:
                continue
            relative = pure.as_posix()
            if relative != "SKILL.md":
                if (
                    len(pure.parts) < 2
                    or pure.parts[0] not in _ALLOWED_COMPANION_ROOTS
                ):
                    raise WorkspaceViolation(
                        "Skill files must be SKILL.md or live under assets/, references/, or scripts/"
                    )
            relative_files[relative] = (root / raw_path).read_bytes()

        try:
            markdown = relative_files["SKILL.md"].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceViolation("SKILL.md must be UTF-8 text") from exc
        if len(markdown) > 50_000:
            raise WorkspaceViolation("SKILL.md exceeds the 50,000-character limit")
        name, description = parse_skill_frontmatter(markdown)
        bundle_path = f"skills/{skill_slug(name)}"
        return ImportedAgentSkill(
            name=name,
            description=description,
            bundle_path=bundle_path,
            skill_markdown=markdown,
            files={
                f"{bundle_path}/{relative}": content
                for relative, content in relative_files.items()
            },
        )
