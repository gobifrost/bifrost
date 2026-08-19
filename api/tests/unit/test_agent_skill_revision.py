"""The Agent Skill revision digest.

The revision identifies Skill *content* so a harness can compare a cached Skill
against a live Agent without re-downloading it. The properties that make that
safe are asserted here: it changes when readable content changes, it is stable
across storage tiers, and path/content boundaries cannot be confused.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.agent_skills import (
    compute_agent_skill_revision,
    compute_skill_revision_from_files,
    refresh_agent_skill_revision,
    resolve_agent_skill_revision,
)


def _inline_agent(**overrides):
    """An Agent with no bundle — its Skill is the projected SKILL.md."""
    base = dict(
        name="Test Agent",
        description="does things",
        system_prompt="Do the thing.",
        bundle_path=None,
        solution_id=None,
        created_by="someone@example.com",
        skill_revision=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestRevisionFromFiles:
    def test_same_content_same_revision(self) -> None:
        files = {"SKILL.md": b"# Skill\n", "references/a.md": b"alpha"}
        assert compute_skill_revision_from_files(files) == (
            compute_skill_revision_from_files(dict(files))
        )

    def test_key_order_does_not_matter(self) -> None:
        """Storage listing order must not change the digest."""
        a = {"SKILL.md": b"x", "b.md": b"y", "a.md": b"z"}
        b = {"a.md": b"z", "SKILL.md": b"x", "b.md": b"y"}
        assert compute_skill_revision_from_files(a) == (
            compute_skill_revision_from_files(b)
        )

    def test_changed_content_changes_revision(self) -> None:
        before = compute_skill_revision_from_files({"SKILL.md": b"one"})
        after = compute_skill_revision_from_files({"SKILL.md": b"two"})
        assert before != after

    def test_added_file_changes_revision(self) -> None:
        before = compute_skill_revision_from_files({"SKILL.md": b"x"})
        after = compute_skill_revision_from_files(
            {"SKILL.md": b"x", "references/new.md": b""}
        )
        assert before != after, "an added empty file must still change the revision"

    def test_renamed_file_changes_revision(self) -> None:
        before = compute_skill_revision_from_files({"a.md": b"same"})
        after = compute_skill_revision_from_files({"b.md": b"same"})
        assert before != after

    def test_path_and_content_boundaries_cannot_be_confused(self) -> None:
        """Length prefixing keeps the digest stream unambiguous.

        Without it, moving bytes between the name and the content of adjacent
        entries could produce the same stream and collide.
        """
        a = compute_skill_revision_from_files({"ab": b"cd"})
        b = compute_skill_revision_from_files({"a": b"bcd"})
        assert a != b

    def test_revision_is_a_sha256_hex_digest(self) -> None:
        revision = compute_skill_revision_from_files({"SKILL.md": b"x"})
        assert len(revision) == 64
        assert all(c in "0123456789abcdef" for c in revision)


@pytest.mark.asyncio
class TestRevisionFromAgent:
    async def test_inline_agent_digest_covers_projected_markdown(self) -> None:
        agent = _inline_agent()
        with patch(
            "src.services.agent_skills.get_agent_skill_markdown",
            new=AsyncMock(return_value="# Test Agent\nDo the thing."),
        ):
            revision = await compute_agent_skill_revision(agent)

        assert revision == compute_skill_revision_from_files(
            {"SKILL.md": b"# Test Agent\nDo the thing."}
        ), "an inline Agent's digest must equal the file-based digest of its SKILL.md"

    async def test_changing_the_prompt_changes_the_revision(self) -> None:
        agent = _inline_agent()
        with patch(
            "src.services.agent_skills.get_agent_skill_markdown",
            new=AsyncMock(return_value="first"),
        ):
            before = await compute_agent_skill_revision(agent)
        with patch(
            "src.services.agent_skills.get_agent_skill_markdown",
            new=AsyncMock(return_value="second"),
        ):
            after = await compute_agent_skill_revision(agent)
        assert before != after

    async def test_bundled_agent_digest_includes_companion_files(self) -> None:
        agent = _inline_agent(bundle_path="skills/demo")
        storage = SimpleNamespace(
            read=AsyncMock(side_effect=lambda path: b"companion-bytes")
        )
        with (
            patch(
                "src.services.agent_skills.get_agent_skill_markdown",
                new=AsyncMock(return_value="# Demo"),
            ),
            patch(
                "src.services.agent_skills._bundle_files",
                new=AsyncMock(
                    return_value=(storage, [("references/a.md", "skills/demo/references/a.md")])
                ),
            ),
        ):
            revision = await compute_agent_skill_revision(agent)

        assert revision == compute_skill_revision_from_files(
            {"SKILL.md": b"# Demo", "references/a.md": b"companion-bytes"}
        ), "the same content must digest identically whichever route computed it"

    async def test_revision_is_stable_across_storage_tiers(self) -> None:
        """An uploaded Agent and a Solution Agent with identical content match.

        The digest deliberately excludes storage paths and agent ids, so moving
        a bundle between tiers does not invent a new revision.
        """
        revisions = []
        for solution_id in (None, "a-solution-id"):
            agent = _inline_agent(bundle_path="skills/demo", solution_id=solution_id)
            storage = SimpleNamespace(read=AsyncMock(return_value=b"same"))
            with (
                patch(
                    "src.services.agent_skills.get_agent_skill_markdown",
                    new=AsyncMock(return_value="# Demo"),
                ),
                patch(
                    "src.services.agent_skills._bundle_files",
                    new=AsyncMock(return_value=(storage, [("a.md", "any/path/a.md")])),
                ),
            ):
                revisions.append(await compute_agent_skill_revision(agent))
        assert revisions[0] == revisions[1]


@pytest.mark.asyncio
class TestRefreshAndResolve:
    async def test_refresh_stamps_the_agent(self) -> None:
        agent = _inline_agent()
        with patch(
            "src.services.agent_skills.get_agent_skill_markdown",
            new=AsyncMock(return_value="x"),
        ):
            revision = await refresh_agent_skill_revision(agent)
        assert agent.skill_revision == revision

    async def test_resolve_returns_the_stored_value_without_recomputing(self) -> None:
        agent = _inline_agent(skill_revision="stored-digest")
        markdown = AsyncMock(return_value="ignored")
        with patch("src.services.agent_skills.get_agent_skill_markdown", new=markdown):
            assert await resolve_agent_skill_revision(agent) == "stored-digest"
        markdown.assert_not_awaited()

    async def test_resolve_computes_when_not_yet_stored(self) -> None:
        """The introducing migration could not backfill, so NULL is expected."""
        agent = _inline_agent(skill_revision=None)
        with patch(
            "src.services.agent_skills.get_agent_skill_markdown",
            new=AsyncMock(return_value="x"),
        ):
            revision = await resolve_agent_skill_revision(agent)
        assert revision == compute_skill_revision_from_files({"SKILL.md": b"x"})

    async def test_resolve_does_not_persist(self) -> None:
        """A read must not require a write transaction."""
        agent = _inline_agent(skill_revision=None)
        with patch(
            "src.services.agent_skills.get_agent_skill_markdown",
            new=AsyncMock(return_value="x"),
        ):
            await resolve_agent_skill_revision(agent)
        assert agent.skill_revision is None
