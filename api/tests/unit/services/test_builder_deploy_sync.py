from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.builder.deploy_sync import (
    BuilderDeployLinkInvalid,
    parse_builder_deploy_link,
    sync_builder_deploy_state,
)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    async def get(self, model, row_id):
        return self.rows.get((model.__name__, row_id))


def test_parse_builder_deploy_link_is_absent_for_ordinary_deploy() -> None:
    assert parse_builder_deploy_link({"force": True}, uuid4()) is None


def test_parse_builder_deploy_link_rejects_partial_link() -> None:
    with pytest.raises(BuilderDeployLinkInvalid, match="incomplete"):
        parse_builder_deploy_link({"builder_turn_id": str(uuid4())}, uuid4())


@pytest.mark.asyncio
async def test_successful_deploy_updates_turn_build_and_preview_revision() -> None:
    solution_id = uuid4()
    session_id = uuid4()
    turn_id = uuid4()
    revision_id = uuid4()
    build_id = uuid4()
    link = parse_builder_deploy_link(
        {
            "builder_turn_id": str(turn_id),
            "source_revision_id": str(revision_id),
        },
        solution_id,
    )
    assert link is not None
    turn = SimpleNamespace(
        session_id=session_id,
        output_revision_id=revision_id,
        status="queued",
        error=None,
        completed_at=None,
        build_job_id=None,
    )
    project = SimpleNamespace(deployed_revision_id=None)
    revision = SimpleNamespace(solution_id=solution_id)
    session = SimpleNamespace(solution_id=solution_id)
    db = _Db(
        {
            ("SolutionBuilderTurn", turn_id): turn,
            ("SolutionBuilderProject", solution_id): project,
            ("SolutionSourceRevision", revision_id): revision,
            ("SolutionBuilderSession", session_id): session,
        }
    )

    await sync_builder_deploy_state(db, link, running=True)
    assert turn.status == "running"

    deploy = SimpleNamespace(
        status="succeeded",
        result={"build_job_ids": [str(build_id)]},
        error=None,
    )
    await sync_builder_deploy_state(db, link, deploy_job=deploy)

    assert turn.status == "succeeded"
    assert turn.build_job_id == build_id
    assert turn.completed_at is not None
    assert project.deployed_revision_id == revision_id


@pytest.mark.asyncio
async def test_builder_deploy_link_cannot_cross_sessions() -> None:
    solution_id = uuid4()
    session_id = uuid4()
    turn_id = uuid4()
    revision_id = uuid4()
    link = parse_builder_deploy_link(
        {
            "builder_turn_id": str(turn_id),
            "source_revision_id": str(revision_id),
        },
        solution_id,
    )
    assert link is not None
    db = _Db(
        {
            ("SolutionBuilderTurn", turn_id): SimpleNamespace(
                session_id=session_id,
                output_revision_id=revision_id,
            ),
            ("SolutionBuilderProject", solution_id): SimpleNamespace(),
            ("SolutionSourceRevision", revision_id): SimpleNamespace(
                solution_id=solution_id
            ),
            ("SolutionBuilderSession", session_id): SimpleNamespace(
                solution_id=uuid4()
            ),
        }
    )

    with pytest.raises(BuilderDeployLinkInvalid, match="crosses"):
        await sync_builder_deploy_state(db, link, running=True)
