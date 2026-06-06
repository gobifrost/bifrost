"""Public entity models expose solution_id so the UI badge can link to the owner."""
import asyncio
from uuid import uuid4

import pytest

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


class _FakeFormRow:
    """ORM-like form row with no `fields` relationship loaded.

    Exercises the FormPublic.compute_form_schema before-validator's
    no-fields-loaded branch, which hand-builds the response dict (the real
    router construction path, not from_attributes).
    """

    def __init__(self, solution_id):
        self.id = uuid4()
        self.name = "my form"
        self.description = None
        self.workflow_id = None
        self.launch_workflow_id = None
        self.default_launch_params = None
        self.allowed_query_params = None
        self.access_level = None
        self.organization_id = None
        self.is_active = True
        self.created_at = None
        self.updated_at = None
        self.solution_id = solution_id
        self.fields = []  # falsy -> no-fields branch


def test_form_validator_populates_solution_id() -> None:
    sol_id = uuid4()
    form = FormPublic.model_validate(_FakeFormRow(sol_id))
    assert form.solution_id == sol_id
    assert form.is_solution_managed is True


def test_form_validator_solution_id_none() -> None:
    form = FormPublic.model_validate(_FakeFormRow(None))
    assert form.solution_id is None
    assert form.is_solution_managed is False


class _FakeAppRow:
    """ORM-like Application row for application_to_public."""

    def __init__(self, solution_id):
        self.id = uuid4()
        self.name = "my app"
        self.slug = "my-app"
        self.description = None
        self.icon = None
        self.organization_id = None
        self.published_at = None
        self.created_at = "2026-06-06T00:00:00+00:00"
        self.updated_at = "2026-06-06T00:00:00+00:00"
        self.created_by = None
        self.is_published = False
        self.has_unpublished_changes = False
        self.access_level = "authenticated"
        self.app_model = "inline_v1"
        self.repo_path = "apps/my-app"
        self.logo_data = None
        self.logo_content_type = None
        self.solution_id = solution_id


class _FakeAppRepo:
    async def get_role_ids(self, _app_id):
        return []


@pytest.mark.parametrize("sol_id", [uuid4(), None])
def test_application_to_public_populates_solution_id(sol_id) -> None:
    from src.routers.applications import application_to_public

    app = asyncio.run(application_to_public(_FakeAppRow(sol_id), _FakeAppRepo()))
    assert app.solution_id == sol_id
    assert app.is_solution_managed is (sol_id is not None)


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
