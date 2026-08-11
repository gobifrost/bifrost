"""Portable Agent Skill export.

Agents are Bifrost's skill-authoring surface.  This module projects an Agent's
instruction body and optional companion bundle files into a conventional
Agent-Skills directory without exposing Bifrost's environment-specific
bindings.
"""

from __future__ import annotations

import json
import re
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import AsyncIterator

import yaml

from src.models.orm.agents import Agent
from src.services.agent_skill_storage import AgentSkillStorage
from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceViolation
from src.services.repo_storage import RepoStorage
from src.services.solutions.storage import SolutionStorage

_UNSAFE_NAME_RE = re.compile(r"[^a-z0-9]+")
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def skill_slug(name: str) -> str:
    """Return a stable Agent-Skills-compatible directory name."""
    slug = _UNSAFE_NAME_RE.sub("-", name.lower()).strip("-")
    return slug[:64].rstrip("-") or "agent-skill"


def parse_skill_frontmatter(markdown: str) -> tuple[str, str]:
    """Return the required portable name and description from ``SKILL.md``."""
    normalized = markdown.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise WorkspaceViolation(
            "SKILL.md must start with YAML frontmatter containing name and description"
        )
    closing = normalized.find("\n---\n", 4)
    if closing < 0:
        raise WorkspaceViolation("SKILL.md frontmatter is not closed")
    try:
        metadata = yaml.safe_load(normalized[4:closing]) or {}
    except yaml.YAMLError as exc:
        raise WorkspaceViolation("SKILL.md frontmatter is invalid YAML") from exc
    if not isinstance(metadata, dict):
        raise WorkspaceViolation("SKILL.md frontmatter must be a YAML mapping")
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip():
        raise WorkspaceViolation("SKILL.md frontmatter requires a non-empty name")
    if not isinstance(description, str) or not description.strip():
        raise WorkspaceViolation(
            "SKILL.md frontmatter requires a non-empty description"
        )
    return name.strip(), description.strip()


def render_skill_markdown(agent: Agent) -> str:
    """Project portable identity and instructions into ``SKILL.md``."""
    name = skill_slug(agent.name)
    description = (agent.description or f"Use the {agent.name} agent").strip()
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "---\n\n"
        f"{agent.system_prompt.rstrip()}\n"
    )


def _relative_bundle_file(bundle_path: str, path: str) -> str | None:
    root = PurePosixPath(bundle_path)
    candidate = PurePosixPath(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        return None
    return relative.as_posix()


def _bundle_storage(
    agent: Agent,
) -> AgentSkillStorage | RepoStorage | SolutionStorage:
    if agent.solution_id is not None:
        return SolutionStorage(agent.solution_id)
    if agent.created_by == "file_sync":
        return RepoStorage()
    return AgentSkillStorage(agent.id)


async def _bundle_files(
    agent: Agent,
) -> tuple[
    AgentSkillStorage | RepoStorage | SolutionStorage | None,
    list[tuple[str, str]],
]:
    if not agent.bundle_path:
        return None, []

    prefix = agent.bundle_path.rstrip("/") + "/"
    storage = _bundle_storage(agent)

    paths = await storage.list(prefix)
    files = [
        (relative, path)
        for path in paths
        if (relative := _relative_bundle_file(agent.bundle_path, path)) is not None
        and relative != "SKILL.md"
    ]
    return storage, sorted(files)


async def get_agent_skill_markdown(agent: Agent) -> str:
    """Return the canonical instructions for an Agent.

    Inline Agents are projected from their editable ``system_prompt``. Bundled
    Agents use their actual ``SKILL.md`` byte-for-byte; the database prompt is a
    compatibility materialization, not a second authoring source.
    """
    if not agent.bundle_path:
        return render_skill_markdown(agent)
    content = await _bundle_storage(agent).read(
        f"{agent.bundle_path.rstrip('/')}/SKILL.md"
    )
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceViolation("SKILL.md must be UTF-8 text") from exc


async def list_agent_skill_files(agent: Agent) -> list[str]:
    """List companion files relative to the portable skill root."""
    _storage, files = await _bundle_files(agent)
    return [relative for relative, _source_path in files]


async def read_agent_skill_file(agent: Agent, relative_path: str) -> bytes:
    """Read one canonical file relative to an Agent's Skill root."""
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in ("", ".", "..") for part in relative.parts)
        or "\\" in relative_path
        or relative.as_posix() != relative_path
    ):
        raise WorkspaceViolation("Skill file path must stay beneath the bundle root")
    if not agent.bundle_path:
        if relative_path == "SKILL.md":
            return render_skill_markdown(agent).encode("utf-8")
        raise FileNotFoundError(relative_path)
    return await _bundle_storage(agent).read(
        f"{agent.bundle_path.rstrip('/')}/{relative_path}"
    )


def _write_member(archive: zipfile.ZipFile, path: str, content: bytes) -> None:
    info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content)


@asynccontextmanager
async def build_agent_skill_archive(agent: Agent) -> AsyncIterator[Path]:
    """Build a bounded, deterministic skill zip and remove it after streaming."""
    limits = WorkspaceLimits()
    storage, bundle_files = await _bundle_files(agent)
    if len(bundle_files) + 1 > limits.max_files:
        raise WorkspaceViolation("skill bundle exceeds max file count")

    with tempfile.TemporaryDirectory(prefix="bifrost-agent-skill-") as tmp:
        markdown_text = await get_agent_skill_markdown(agent)
        portable_name = (
            parse_skill_frontmatter(markdown_text)[0]
            if agent.bundle_path
            else agent.name
        )
        root = skill_slug(portable_name)
        destination = Path(tmp) / f"{root}.zip"
        total_bytes = 0
        with zipfile.ZipFile(destination, "w") as archive:
            markdown = markdown_text.encode("utf-8")
            total_bytes += len(markdown)
            _write_member(archive, f"{root}/SKILL.md", markdown)

            if storage is not None:
                for relative, source_path in bundle_files:
                    content = await storage.read(source_path)  # type: ignore[union-attr]
                    if len(content) > limits.max_file_bytes:
                        raise WorkspaceViolation(
                            f"skill file exceeds per-file byte limit: {relative}"
                        )
                    total_bytes += len(content)
                    if total_bytes > limits.max_total_bytes:
                        raise WorkspaceViolation("skill bundle exceeds total byte limit")
                    _write_member(archive, f"{root}/{relative}", content)

        yield destination
