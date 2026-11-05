"""Captured App identity survives retained-source export and SDK resolution."""

from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.services.application_source_resolver import resolve_application_source
from src.services.repo_storage import RepoStorage
from src.services.solutions.capture import (
    SolutionCaptureSelectors,
    SolutionCaptureService,
)
from src.services.solutions.export import build_workspace_zip
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage

pytestmark = pytest.mark.e2e


async def test_captured_app_retained_source_resolves_without_remapping(db_session):
    slug = f"captured-sdk-{uuid4().hex[:8]}"
    solution = Solution(id=uuid4(), slug=slug, name="Captured SDK")
    app = Application(
        id=uuid4(),
        name="Captured App",
        slug=slug,
        repo_path=f"apps/{slug}",
        app_model="standalone_v2",
        dependencies={"react": "^18.3.1"},
    )
    db_session.add_all([solution, app])
    await db_session.flush()
    original_id = app.id
    files = {
        "package.json": b'{"name":"captured-app"}',
        "index.html": b"<div id='root'></div>",
        "src/main.tsx": b"export const captured = true;\n",
    }
    repo = RepoStorage()
    storage = SolutionSourceArtifactStorage(solution.id)
    try:
        for path, data in files.items():
            await repo.write(f"{app.repo_path}/{path}", data)
        capture = SolutionCaptureService(db_session, repo)
        await capture.capture(
            solution,
            SolutionCaptureSelectors(
                workflows=[], tables=[], apps=[app.id], forms=[], agents=[],
                claims=[], configs=[],
            ),
        )
        await db_session.refresh(app)
        assert app.id == original_id
        assert app.solution_id == solution.id
        bundle = await capture.bundle_for(solution)
        archive = build_workspace_zip(bundle)
        await storage.write(archive)

        source = await resolve_application_source(app)

        assert source.files == files
        assert source.dependencies == {"react": "^18.3.1"}
        assert source.source_kind == "solution"
        assert await storage.read() == archive
    finally:
        await storage.delete()
        for path in files:
            await repo.delete(f"{app.repo_path}/{path}")
