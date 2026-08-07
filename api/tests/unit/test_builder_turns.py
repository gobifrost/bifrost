"""Builder scaffold + turn lifecycle.

Two layers, deliberately separated:

* the pure workspace functions (scaffold shape, determinism, validation) are
  called directly — no DB, no S3, no lock;
* the turn lifecycle runs against the real ``db_session`` (the established
  pattern for these "unit" tests) with S3 and the write lock faked, so the
  assertions are about lineage and pointer movement rather than infrastructure.

The lock is replaced with a null context manager by default; the contention
test swaps in one that raises, which is exactly what a concurrent writer looks
like from inside :meth:`BuilderTurnService.run_turn`.
"""

from __future__ import annotations

import contextlib
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Conversation
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder import turns as turns_module
from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot, safe_extract_zip
from src.services.builder.scaffold import (
    build_initial_workspace,
    validate_workspace,
    zip_workspace,
)
from src.services.builder.turns import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    BuilderTurnConflict,
    BuilderTurnService,
    WorkspaceInvalid,
)
from src.services.solutions.write_lock import SolutionWriteLockHeld

# ── pure workspace functions ────────────────────────────────────────────────


def test_scaffold_is_a_valid_workspace(tmp_path: Path):
    workspace = tmp_path / "ws"
    build_initial_workspace(workspace, slug="my-sol", name="My Solution")

    assert validate_workspace(workspace) == []
    assert (workspace / "bifrost.solution.yaml").is_file()
    assert (workspace / "workflows").is_dir()
    assert (workspace / "README.md").is_file()


def test_scaffold_survives_a_zip_round_trip(tmp_path: Path):
    """The workflows/ directory must exist again after extraction.

    ``safe_extract_zip`` refuses directory members, so a directory only comes
    back if a file inside it does. This is the test that catches a scaffold
    that quietly loses ``workflows/`` the first time a turn runs.
    """
    source = tmp_path / "src"
    build_initial_workspace(source, slug="my-sol", name="My Solution")
    sha = zip_workspace(source, tmp_path / "src.zip")

    extracted = tmp_path / "out"
    extracted.mkdir()
    safe_extract_zip(tmp_path / "src.zip", extracted, WorkspaceLimits())

    assert validate_workspace(extracted) == []
    assert (extracted / "workflows").is_dir()
    assert zip_workspace(extracted, tmp_path / "out.zip") == sha


def test_zip_is_deterministic_across_builds(tmp_path: Path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    build_initial_workspace(first, slug="s", name="S")
    build_initial_workspace(second, slug="s", name="S")

    assert zip_workspace(first, tmp_path / "a.zip") == zip_workspace(second, tmp_path / "b.zip")


def test_zip_sha_changes_when_content_changes(tmp_path: Path):
    workspace = tmp_path / "ws"
    build_initial_workspace(workspace, slug="s", name="S")
    before = zip_workspace(workspace, tmp_path / "before.zip")

    (workspace / "workflows" / "hello.py").write_text("def run():\n    return 1\n")
    after = zip_workspace(workspace, tmp_path / "after.zip")

    assert before != after


def test_zip_members_carry_a_fixed_timestamp(tmp_path: Path):
    workspace = tmp_path / "ws"
    build_initial_workspace(workspace, slug="s", name="S")
    zip_workspace(workspace, tmp_path / "ws.zip")

    with zipfile.ZipFile(tmp_path / "ws.zip") as archive:
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_validate_reports_a_missing_descriptor(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()

    errors = validate_workspace(empty)

    assert len(errors) == 1
    assert "bifrost.solution.yaml" in errors[0]


def test_validate_reports_a_descriptor_missing_slug(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "bifrost.solution.yaml").write_text("name: No Slug\n")

    assert validate_workspace(workspace) != []


def test_validate_reports_unparseable_yaml(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "bifrost.solution.yaml").write_text("slug: [unclosed\n")

    assert validate_workspace(workspace) != []


# ── fakes ───────────────────────────────────────────────────────────────────


class FakeRevisionStorage:
    """In-memory stand-in for the S3-backed revision store, keyed by revision id."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    async def write_from_path(self, revision_id: UUID | str, path: Path) -> None:
        self.blobs[str(revision_id)] = path.read_bytes()

    async def copy_to_path(self, revision_id: UUID | str, dest: Path) -> bool:
        blob = self.blobs.get(str(revision_id))
        if blob is None:
            return False
        dest.write_bytes(blob)
        return True


@contextlib.asynccontextmanager
async def _null_lock(solution_id: UUID) -> AsyncIterator[None]:
    yield


@contextlib.asynccontextmanager
async def _held_lock(solution_id: UUID) -> AsyncIterator[None]:
    raise SolutionWriteLockHeld(str(solution_id))
    yield  # pragma: no cover - unreachable, satisfies the generator contract


@pytest.fixture
def blobs() -> dict[str, bytes]:
    return {}


@pytest.fixture(autouse=True)
def fake_infrastructure(monkeypatch: pytest.MonkeyPatch, blobs: dict[str, bytes]) -> None:
    monkeypatch.setattr(turns_module, "solution_write_lock", _null_lock)
    monkeypatch.setattr(
        BuilderTurnService,
        "_storage",
        lambda self, solution_id: FakeRevisionStorage(blobs),
    )


@pytest_asyncio.fixture
async def solution(db_session: AsyncSession) -> Solution:
    row = Solution(id=uuid4(), slug=f"builder-{uuid4().hex[:8]}", name="Builder", organization_id=None)
    db_session.add(row)
    await db_session.flush()
    return row


@pytest_asyncio.fixture
async def session_row(db_session: AsyncSession, solution: Solution) -> SolutionBuilderSession:
    # Satisfies ck_users_org_requires_superuser without needing an Organization
    # row: a superuser may have no org (system-account shape).
    user = User(
        id=uuid4(),
        email=f"builder-{uuid4().hex[:8]}@example.com",
        is_superuser=True,
    )
    db_session.add(user)
    conversation = Conversation(id=uuid4(), user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()

    row = SolutionBuilderSession(
        id=uuid4(),
        solution_id=solution.id,
        conversation_id=conversation.id,
        user_id=user.id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest_asyncio.fixture
async def service(db_session: AsyncSession) -> BuilderTurnService:
    return BuilderTurnService(db_session)


async def _seed_project(
    service: BuilderTurnService, solution: Solution
) -> SolutionSourceRevision:
    return await service.create_project(
        solution.id,
        slug="my-sol",
        name="My Solution",
        conversation_id=None,
        user_id=None,
    )


# ── project creation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_stores_revision_zero(
    service: BuilderTurnService,
    db_session: AsyncSession,
    solution: Solution,
    blobs: dict[str, bytes],
    tmp_path: Path,
):
    revision = await _seed_project(service, solution)

    assert revision.summary == "initial scaffold"
    assert revision.parent_revision_id is None
    assert revision.size_bytes > 0

    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert project is not None
    assert project.current_revision_id == revision.id

    # The stored blob is the scaffold, and its sha matches the recorded one.
    stored = tmp_path / "stored.zip"
    stored.write_bytes(blobs[str(revision.id)])
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    safe_extract_zip(stored, extracted, WorkspaceLimits())
    assert validate_workspace(extracted) == []
    assert zip_workspace(extracted, tmp_path / "re.zip") == revision.source_sha256


# ── turn lifecycle ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_that_changes_content_creates_a_revision(
    service: BuilderTurnService,
    db_session: AsyncSession,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    base = await _seed_project(service, solution)

    async def mutate(workspace: WorkspaceRoot) -> None:
        workspace.write_file("workflows/hello.py", b"def run():\n    return 1\n")

    turn = await service.run_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        mutate=mutate,
        summary="add hello",
    )

    assert turn.status == STATUS_SUCCEEDED
    assert turn.base_revision_id == base.id
    assert turn.output_revision_id is not None
    assert turn.output_revision_id != base.id
    assert turn.started_at is not None and turn.completed_at is not None

    output = await db_session.get(SolutionSourceRevision, turn.output_revision_id)
    assert output is not None
    assert output.parent_revision_id == base.id
    assert output.conversation_id == session_row.conversation_id
    assert output.summary == "add hello"
    assert output.source_sha256 != base.source_sha256

    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert project is not None
    assert project.current_revision_id == turn.output_revision_id


@pytest.mark.asyncio
async def test_no_op_turn_adds_no_revision(
    service: BuilderTurnService,
    db_session: AsyncSession,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    """A turn whose mutation changes nothing must not fork history.

    The short circuit is a sha comparison, so writing a file back with the
    content it already had still counts as a no-op.
    """
    base = await _seed_project(service, solution)
    readme = "# My Solution\n\nBuilt with the Bifrost Solution builder.\n"

    async def mutate(workspace: WorkspaceRoot) -> None:
        workspace.write_file("README.md", readme.encode("utf-8"))

    turn = await service.run_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=None,
        mutate=mutate,
    )

    assert turn.status == STATUS_SUCCEEDED
    assert turn.output_revision_id == base.id

    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert project is not None
    assert project.current_revision_id == base.id


@pytest.mark.asyncio
async def test_failed_mutation_leaves_the_pointer_unchanged(
    service: BuilderTurnService,
    db_session: AsyncSession,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    base = await _seed_project(service, solution)

    async def mutate(workspace: WorkspaceRoot) -> None:
        workspace.write_file("workflows/partial.py", b"# half-written\n")
        raise RuntimeError("agent exploded")

    with pytest.raises(RuntimeError, match="agent exploded"):
        await service.run_turn(
            solution.id,
            session_id=session_row.id,
            requested_by=None,
            mutate=mutate,
        )

    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert project is not None
    assert project.current_revision_id == base.id


@pytest.mark.asyncio
async def test_failed_turn_is_recorded_on_the_turn_row(
    service: BuilderTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    await _seed_project(service, solution)

    async def mutate(workspace: WorkspaceRoot) -> None:
        raise RuntimeError("agent exploded")

    with pytest.raises(RuntimeError):
        await service.run_turn(
            solution.id,
            session_id=session_row.id,
            requested_by=None,
            mutate=mutate,
        )

    turn = await _latest_turn(service, session_row.id)
    assert turn.status == STATUS_FAILED
    assert turn.error is not None and "agent exploded" in turn.error
    assert turn.output_revision_id is None
    assert turn.completed_at is not None


@pytest.mark.asyncio
async def test_turn_that_breaks_the_workspace_fails_validation(
    service: BuilderTurnService,
    db_session: AsyncSession,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    base = await _seed_project(service, solution)

    async def mutate(workspace: WorkspaceRoot) -> None:
        workspace.delete_file("bifrost.solution.yaml")

    with pytest.raises(WorkspaceInvalid):
        await service.run_turn(
            solution.id,
            session_id=session_row.id,
            requested_by=None,
            mutate=mutate,
        )

    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert project is not None
    assert project.current_revision_id == base.id


@pytest.mark.asyncio
async def test_temp_directory_is_removed_when_a_turn_fails(
    service: BuilderTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    await _seed_project(service, solution)
    seen: list[Path] = []

    async def mutate(workspace: WorkspaceRoot) -> None:
        seen.append(workspace.root)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await service.run_turn(
            solution.id,
            session_id=session_row.id,
            requested_by=None,
            mutate=mutate,
        )

    assert seen, "the mutation never ran"
    assert not seen[0].exists()
    assert not seen[0].parent.exists()


@pytest.mark.asyncio
async def test_concurrent_turn_raises_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
    service: BuilderTurnService,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    await _seed_project(service, solution)
    monkeypatch.setattr(turns_module, "solution_write_lock", _held_lock)

    async def mutate(workspace: WorkspaceRoot) -> None:  # pragma: no cover - never runs
        raise AssertionError("the mutation must not run while the lock is held")

    with pytest.raises(BuilderTurnConflict):
        await service.run_turn(
            solution.id,
            session_id=session_row.id,
            requested_by=None,
            mutate=mutate,
        )


# ── undo ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_undo_restores_content_and_records_its_source(
    service: BuilderTurnService,
    db_session: AsyncSession,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    base = await _seed_project(service, solution)

    async def add_file(workspace: WorkspaceRoot) -> None:
        workspace.write_file("workflows/hello.py", b"def run():\n    return 1\n")

    changed = await service.run_turn(
        solution.id,
        session_id=session_row.id,
        requested_by=None,
        mutate=add_file,
    )
    assert changed.output_revision_id != base.id

    undone = await service.undo(
        solution.id,
        session_id=session_row.id,
        requested_by=session_row.user_id,
        to_revision_id=base.id,
    )

    assert undone.status == STATUS_SUCCEEDED
    assert undone.base_revision_id == changed.output_revision_id

    restored = await db_session.get(SolutionSourceRevision, undone.output_revision_id)
    assert restored is not None
    assert restored.restored_from_revision_id == base.id
    assert restored.parent_revision_id == changed.output_revision_id
    # History is append-only: undo writes a NEW revision whose content matches
    # the target, it does not rewind the pointer to the old row.
    assert restored.id != base.id
    assert restored.source_sha256 == base.source_sha256

    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert project is not None
    assert project.current_revision_id == restored.id


@pytest.mark.asyncio
async def test_undo_to_the_current_revision_is_a_no_op(
    service: BuilderTurnService,
    db_session: AsyncSession,
    solution: Solution,
    session_row: SolutionBuilderSession,
):
    base = await _seed_project(service, solution)

    undone = await service.undo(
        solution.id,
        session_id=session_row.id,
        requested_by=None,
        to_revision_id=base.id,
    )

    assert undone.status == STATUS_SUCCEEDED
    assert undone.output_revision_id == base.id

    project = await db_session.get(SolutionBuilderProject, solution.id)
    assert project is not None
    assert project.current_revision_id == base.id


async def _latest_turn(service: BuilderTurnService, session_id: UUID) -> SolutionBuilderTurn:
    result = await service.db.execute(
        select(SolutionBuilderTurn)
        .where(SolutionBuilderTurn.session_id == session_id)
        .order_by(desc(SolutionBuilderTurn.created_at))
        .limit(1)
    )
    return result.scalar_one()
