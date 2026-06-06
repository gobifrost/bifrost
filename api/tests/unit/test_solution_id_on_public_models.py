"""Public entity models expose solution_id so the UI badge can link to the owner."""
from uuid import uuid4

from src.models.contracts.agents import AgentPublic
from src.models.contracts.applications import ApplicationPublic
from src.models.contracts.forms import FormPublic
from src.models.contracts.tables import TablePublic
from src.models.contracts.workflows import WorkflowMetadata


def test_public_models_expose_solution_id() -> None:
    for model in (AgentPublic, ApplicationPublic, FormPublic, TablePublic, WorkflowMetadata):
        assert "solution_id" in model.model_fields, f"{model.__name__} missing solution_id"


def test_solution_id_populates_from_value() -> None:
    sol_id = uuid4()
    table = TablePublic.model_validate(
        {
            "id": uuid4(),
            "name": "things",
            "organization_id": uuid4(),
            "created_at": "2026-06-06T00:00:00+00:00",
            "updated_at": "2026-06-06T00:00:00+00:00",
            "created_by": "dev@gobifrost.com",
            "solution_id": sol_id,
        }
    )
    assert table.solution_id == sol_id


def test_solution_id_defaults_none() -> None:
    table = TablePublic.model_validate(
        {
            "id": uuid4(),
            "name": "things",
            "organization_id": None,
            "created_at": "2026-06-06T00:00:00+00:00",
            "updated_at": "2026-06-06T00:00:00+00:00",
            "created_by": None,
        }
    )
    assert table.solution_id is None
