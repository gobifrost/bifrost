"""Form execution contexts preserve form data while choosing the right actor."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock
from uuid import uuid4

import pytest

from shared import form_execution
from shared.run_lineage import RunLineage
from src.core.principal import UserPrincipal
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller


def _caller(*, home, organization_grants=()) -> Caller:
    principal = UserPrincipal(
        user_id=uuid4(), email="form-scope@contoso.example", organization_id=home
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=home,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
            role_grants=tuple(organization_grants),
        ),
    )


@pytest.mark.asyncio
async def test_global_form_uses_reachable_requested_organization(monkeypatch):
    home = uuid4()
    selected = uuid4()
    caller = _caller(
        home=home,
        organization_grants=(
            RoleGrant(
                role_id=uuid4(),
                permissions=frozenset({"workflows.execute"}),
                boundaries=(Boundary(BoundaryKind.ORGANIZATION, selected),),
            ),
        ),
    )
    monkeypatch.setattr(
        "src.services.authorization.enforce.load_caller", AsyncMock(return_value=caller)
    )

    scope = await form_execution.resolve_form_execution_scope(
        AsyncMock(),
        user=caller.principal,
        form_organization_id=None,
        requested_organization_id=selected,
    )

    assert scope.organization_id == selected
    assert scope.caller is caller


@pytest.mark.asyncio
async def test_scoped_form_rejects_a_different_requested_organization():
    form_organization = uuid4()

    with pytest.raises(form_execution.FormExecutionScopeError) as exc_info:
        await form_execution.resolve_form_execution_scope(
            AsyncMock(),
            user=SimpleNamespace(embed=False),
            form_organization_id=form_organization,
            requested_organization_id=uuid4(),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_embed_form_cannot_select_an_organization():
    with pytest.raises(form_execution.FormExecutionScopeError) as exc_info:
        await form_execution.resolve_form_execution_scope(
            AsyncMock(),
            user=SimpleNamespace(embed=True),
            form_organization_id=None,
            requested_organization_id=uuid4(),
            embed_organization_id=uuid4(),
        )

    assert exc_info.value.status_code == 403


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
        runtime_authority=None,
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


@pytest.mark.asyncio
async def test_global_form_provider_uses_the_selected_execution_organization(monkeypatch):
    from shared import form_provider
    from src.repositories import workflows
    from src.services.execution import service

    selected_organization_id = uuid4()
    lineage = RunLineage(uuid4(), uuid4(), None)
    provider_id = uuid4()
    form = SimpleNamespace(id=uuid4(), organization_id=None, solution_id=None)
    field = SimpleNamespace(data_provider_id=provider_id, data_provider_inputs={}, auto_fill={})
    provider = SimpleNamespace(
        id=provider_id,
        organization_id=None,
        is_active=True,
        type="data_provider",
    )
    repo = SimpleNamespace(resolve=AsyncMock(return_value=provider))
    workflow_repository = MagicMock(return_value=repo)
    monkeypatch.setattr(workflows, "WorkflowRepository", workflow_repository)
    context = SimpleNamespace(user_id=str(lineage.run_user_id))
    create_context = AsyncMock(return_value=context)
    monkeypatch.setattr(form_provider, "create_form_execution_context", create_context)
    monkeypatch.setattr(
        service,
        "run_workflow",
        AsyncMock(return_value=SimpleNamespace(result=[])),
    )
    user = SimpleNamespace(embed=True, user_id=uuid4(), is_superuser=False)
    db = AsyncMock()

    await form_provider.execute_form_field_provider(
        db=db,
        form=form,
        field=field,
        user=user,
        caller_org_id=selected_organization_id,
        browser_inputs={},
        lineage=lineage,
    )

    workflow_repository.assert_called_once_with(
        db, org_id=selected_organization_id, is_superuser=True
    )
    create_context.assert_awaited_once_with(
        db,
        user=user,
        lineage=lineage,
        execution_id=ANY,
        organization_id=selected_organization_id,
        is_platform_admin=False,
    )


@pytest.mark.asyncio
async def test_agent_submitting_a_form_keeps_agent_origin(monkeypatch):
    user = UserPrincipal(user_id=uuid4(), email="form-agent@example.com", name="Agent Actor", organization_id=None)
    agent_id = uuid4()
    user.runtime_authority = SimpleNamespace(execution_origin="agent", agent_id=agent_id)
    from shared.execution_provenance import ExecutionProvenance
    monkeypatch.setattr(form_execution, "load_execution_provenance", AsyncMock(return_value=ExecutionProvenance("agent", agent_id)))
    context = await form_execution.create_form_execution_context(
        AsyncMock(), user=user, lineage=RunLineage(user.user_id, user.user_id, None),
        execution_id=str(uuid4()), organization_id=None, is_platform_admin=False,
    )
    assert (context.execution_origin, context.agent_id, context.is_agent) == ("agent", str(agent_id), True)
