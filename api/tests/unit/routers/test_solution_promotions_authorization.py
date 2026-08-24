from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.solution_builder import PromotionTargetRequest
from src.services.authorization import AuthorizationBoundary
from src.routers import solution_promotions


@pytest.mark.asyncio
async def test_company_publish_resolves_destination_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_org_id = uuid4()
    destination_org_id = uuid4()
    requester = SimpleNamespace(user_id=uuid4())
    source_authorization = SimpleNamespace(
        requester=requester,
        request_id="request-1",
    )
    destination_authorization = Mock()
    resolve = AsyncMock(return_value=destination_authorization)
    monkeypatch.setattr(solution_promotions, "resolve_authorization_context", resolve)

    body = PromotionTargetRequest(
        target="company",
        target_organization_id=destination_org_id,
        runtime_mode="isolated",
    )
    await solution_promotions._destination_authorization(
        source_authorization,
        SimpleNamespace(),
        SimpleNamespace(organization_id=source_org_id),
        body,
    )

    assert resolve.await_args.kwargs["selected_boundary"] == (
        AuthorizationBoundary.organization(destination_org_id)
    )
    destination_authorization.require.assert_called_once_with(
        "solutions.publish.execute"
    )


@pytest.mark.asyncio
async def test_global_publish_requires_optional_grant_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_authorization = SimpleNamespace(
        requester=SimpleNamespace(user_id=uuid4()),
        request_id=None,
    )
    destination_authorization = Mock()
    monkeypatch.setattr(
        solution_promotions,
        "resolve_authorization_context",
        AsyncMock(return_value=destination_authorization),
    )

    await solution_promotions._destination_authorization(
        source_authorization,
        SimpleNamespace(),
        SimpleNamespace(organization_id=uuid4()),
        PromotionTargetRequest(
            target="global",
            runtime_mode="isolated",
            approve_role_creation=True,
            allow_global_repo_access=True,
            approved_connection_names=["HaloPSA"],
        ),
    )

    assert [call.args[0] for call in destination_authorization.require.call_args_list] == [
        "solutions.publish.execute",
        "roles.readwrite",
        "repository.access.readwrite",
        "integrations.readwrite",
    ]


@pytest.mark.asyncio
async def test_platform_boundary_does_not_expose_customer_source() -> None:
    authorization = Mock()
    authorization.selected_boundary = AuthorizationBoundary.platform()
    authorization.has_capability.return_value = False

    with pytest.raises(HTTPException) as exc:
        await solution_promotions._require_source_review(
            authorization,
            SimpleNamespace(),
            SimpleNamespace(organization_id=uuid4()),
        )

    assert exc.value.status_code == 404
    authorization.require.assert_called_once_with("solutions.publish.read")
