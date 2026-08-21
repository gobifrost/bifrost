"""Builder contract shapes should stay agentless."""

from datetime import datetime, timezone
from uuid import uuid4

from src.models.contracts.solution_builder import (
    BuilderProjectDTO,
    BuilderSessionDTO,
    PrivateSolutionCreate,
)


def test_builder_session_dto_is_agentless() -> None:
    dto = BuilderSessionDTO(
        id=uuid4(),
        solution_id=uuid4(),
        conversation_id=uuid4(),
        user_id=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    assert "builder_agent_id" not in BuilderSessionDTO.model_fields
    assert dto.model_dump()["solution_id"] == dto.solution_id


def test_builder_contracts_accept_organization_targets() -> None:
    create = PrivateSolutionCreate(
        slug="alpha",
        name="Alpha",
        target_kind="organization",
    )
    project = BuilderProjectDTO(
        solution_id=uuid4(),
        promotion_status="none",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        target_kind="organization",
    )

    assert create.target_kind == "organization"
    assert project.target_kind == "organization"
