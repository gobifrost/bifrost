"""Form execution contexts preserve form data while choosing the right actor."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock
from uuid import uuid4

import pytest

from shared import form_execution
from shared.run_lineage import RunLineage


@pytest.mark.asyncio
async def test_embed_form_context_uses_accepted_identity_and_preserves_form_data(
    monkeypatch,
):
    lineage = RunLineage(uuid4(), uuid4(), None)
    identity_context = SimpleNamespace(
        user_id=str(lineage.run_user_id),
        name="Contoso Form Identity",
        email="forms@contoso.example",
        is_platform_admin=False,
    )
    create_context = AsyncMock(return_value=identity_context)
    monkeypatch.setattr(form_execution, "create_unattended_context", create_context)
    organization_id = uuid4()
    user = SimpleNamespace(
        embed=True,
        user_id=uuid4(),
        name="embed@internal.gobifrost.com",
        email="embed@internal.gobifrost.com",
        is_superuser=False,
        verified_context={"customer": "Contoso"},
    )

    context = await form_execution.create_form_execution_context(
        AsyncMock(),
        user=user,
        lineage=lineage,
        execution_id="execution-1",
        organization_id=organization_id,
        is_platform_admin=True,
        startup={"launch": "trusted"},
        form_inputs={"requester": "Ada"},
    )

    create_context.assert_awaited_once_with(
        ANY,
        lineage=lineage,
        execution_id="execution-1",
        org_id=str(organization_id),
        is_function_key=False,
    )
    assert context.user_id == str(lineage.run_user_id)
    assert context.name == "Contoso Form Identity"
    assert context.startup == {"launch": "trusted"}
    assert context.form_inputs == {"requester": "Ada"}
    assert context.embed == {"customer": "Contoso"}


@pytest.mark.asyncio
async def test_human_form_context_keeps_the_human_actor(monkeypatch):
    create_context = AsyncMock()
    monkeypatch.setattr(form_execution, "create_unattended_context", create_context)
    organization_id = uuid4()
    user_id = uuid4()
    user = SimpleNamespace(
        embed=False,
        user_id=user_id,
        name="Fabrikam Operator",
        email="operator@fabrikam.example",
        is_superuser=False,
        verified_context=None,
    )

    context = await form_execution.create_form_execution_context(
        AsyncMock(),
        user=user,
        lineage=RunLineage(user_id, user_id, None),
        execution_id="execution-2",
        organization_id=organization_id,
        is_platform_admin=False,
    )

    create_context.assert_not_awaited()
    assert context.user_id == str(user_id)
    assert context.name == "Fabrikam Operator"
    assert context.email == "operator@fabrikam.example"

@pytest.mark.asyncio
async def test_embed_provider_runs_with_the_accepted_lineage(monkeypatch):
    from shared import form_provider
    from src.repositories import workflows
    from src.services.execution import service

    organization_id = uuid4()
    lineage = RunLineage(uuid4(), uuid4(), None)
    provider_id = uuid4()
    form = SimpleNamespace(
        id=uuid4(), organization_id=organization_id, solution_id=None
    )
    field = SimpleNamespace(
        data_provider_id=provider_id,
        data_provider_inputs={},
        auto_fill={},
    )
    provider = SimpleNamespace(
        id=provider_id,
        organization_id=organization_id,
        is_active=True,
        type="data_provider",
    )
    repo = SimpleNamespace(resolve=AsyncMock(return_value=provider))
    workflow_repository = MagicMock(return_value=repo)
    monkeypatch.setattr(workflows, "WorkflowRepository", workflow_repository)
    context = SimpleNamespace(user_id=str(lineage.run_user_id))
    create_context = AsyncMock(return_value=context)
    monkeypatch.setattr(form_provider, "create_form_execution_context", create_context)
    run_workflow = AsyncMock(
        return_value=SimpleNamespace(result=[{"value": "contoso", "label": "Contoso"}])
    )
    monkeypatch.setattr(service, "run_workflow", run_workflow)
    user = SimpleNamespace(
        embed=True,
        user_id=uuid4(),
        is_superuser=False,
    )

    options = await form_provider.execute_form_field_provider(
        db=AsyncMock(),
        form=form,
        field=field,
        user=user,
        caller_org_id=organization_id,
        browser_inputs={},
        lineage=lineage,
    )

    create_context.assert_awaited_once_with(
        ANY,
        user=user,
        lineage=lineage,
        execution_id=ANY,
        organization_id=organization_id,
        is_platform_admin=False,
    )
    run_workflow.assert_awaited_once_with(
        context=context,
        workflow_id=str(provider_id),
        input_data={},
        form_id=str(form.id),
        transient=True,
        sync=True,
        lineage=lineage,
    )
    assert options == [
        {"value": "contoso", "label": "Contoso", "description": None, "metadata": None}
    ]
