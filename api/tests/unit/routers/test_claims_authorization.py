from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.claims import _resolve_target_org, list_claims
from src.services.authorization import AuthorizationBoundary


class _EmptyResult:
    def scalars(self) -> _EmptyResult:
        return self

    def all(self) -> list[object]:
        return []


class _EmptyDb:
    async def execute(self, _query: object) -> _EmptyResult:
        return _EmptyResult()


def _authorization(boundary: AuthorizationBoundary) -> Mock:
    authorization = Mock()
    authorization.selected_boundary = boundary
    return authorization


def test_claim_target_uses_the_selected_exact_organization() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        AuthorizationBoundary.organization(organization_id)
    )

    assert _resolve_target_org(authorization, None) == organization_id
    authorization.require_resource_boundary.assert_called_once_with(organization_id)


def test_claim_target_rejects_scope_that_disagrees_with_selected_boundary() -> None:
    authorization = _authorization(AuthorizationBoundary.organization(uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        _resolve_target_org(authorization, str(uuid4()))

    assert exc_info.value.status_code == 409


def test_managed_organizations_is_not_a_claim_mutation_identity() -> None:
    authorization = _authorization(AuthorizationBoundary.managed_organizations())

    with pytest.raises(HTTPException) as exc_info:
        _resolve_target_org(authorization, str(uuid4()))

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_platform_claim_collection_has_no_loose_global_rows() -> None:
    authorization = _authorization(AuthorizationBoundary.platform())

    result = await list_claims(
        ctx=SimpleNamespace(db=_EmptyDb()),
        authorization=authorization,
        scope=None,
    )

    authorization.require_operation.assert_called_once_with("claims.list")
    assert result.claims == []
