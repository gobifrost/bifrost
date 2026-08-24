from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.config import (
    _require_scope_matches_selected_boundary,
    _selected_config_target,
)
from src.services.authorization import AuthorizationBoundary


def _authorization(boundary: AuthorizationBoundary) -> Mock:
    authorization = Mock()
    authorization.selected_boundary = boundary
    return authorization


def test_config_target_is_the_selected_exact_organization() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        AuthorizationBoundary.organization(organization_id)
    )

    assert _selected_config_target(authorization) == organization_id
    _require_scope_matches_selected_boundary(authorization, organization_id)


def test_config_target_is_global_only_in_platform_boundary() -> None:
    authorization = _authorization(AuthorizationBoundary.platform())

    assert _selected_config_target(authorization) is None
    _require_scope_matches_selected_boundary(authorization, None)


def test_config_scope_cannot_override_the_selected_boundary() -> None:
    authorization = _authorization(AuthorizationBoundary.organization(uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        _require_scope_matches_selected_boundary(authorization, uuid4())

    assert exc_info.value.status_code == 409


def test_managed_organizations_is_not_a_config_mutation_identity() -> None:
    authorization = _authorization(AuthorizationBoundary.managed_organizations())

    with pytest.raises(HTTPException) as exc_info:
        _selected_config_target(authorization)

    assert exc_info.value.status_code == 409
