"""Event subscription org-scope rule (``_validate_subscription_scope``)."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.events import _validate_subscription_scope


def test_global_source_may_target_any_org() -> None:
    _validate_subscription_scope(None, uuid4())


def test_any_source_may_target_global() -> None:
    _validate_subscription_scope(uuid4(), None)
    _validate_subscription_scope(None, None)


def test_org_source_may_target_its_own_org() -> None:
    org = uuid4()
    _validate_subscription_scope(org, org)


def test_org_source_may_not_target_another_org() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_subscription_scope(uuid4(), uuid4())
    assert exc.value.status_code == 422
