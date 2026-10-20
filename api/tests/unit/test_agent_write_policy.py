"""Personal Agent edits preserve management-only budget configuration."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.agents import AgentUpdate
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.services.agent_write_policy import enforce_personal_agent_update


@pytest.fixture
def personal_agent():
    return Agent(
        name="Contoso Assistant",
        organization_id=uuid4(),
        owner_user_id=uuid4(),
        access_level=AgentAccessLevel.PRIVATE,
        system_prompt="Help with requests.",
        created_by="operator@contoso.example",
    )


@pytest.mark.parametrize(
    ("field", "budget"),
    [("max_iterations", 100), ("max_token_budget", 1000), ("llm_max_tokens", 1000)],
)
@pytest.mark.parametrize("clear", [False, True])
def test_personal_owner_cannot_set_or_clear_a_budget(personal_agent, field, budget, clear):
    update = AgentUpdate.model_validate({field: None if clear else budget})

    with pytest.raises(HTTPException) as error:
        enforce_personal_agent_update(personal_agent, update)

    assert error.value.status_code == 403
    assert "budget" in error.value.detail
    assert field in error.value.detail


def test_personal_owner_can_edit_description_without_changing_configuration(personal_agent):
    update = AgentUpdate(description="Updated description")

    enforce_personal_agent_update(personal_agent, update)

    assert update.description == "Updated description"
