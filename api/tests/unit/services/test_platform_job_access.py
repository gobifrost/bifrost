from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.orm.platform_jobs import PlatformJob
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.platform_job_access import (
    PlatformJobAccessDenied,
    require_platform_job_access,
)


def _caller(user_id):
    principal = UserPrincipal(user_id=user_id, email="person@example.com", organization_id=None)
    return Caller(principal, AuthorizationContext(
        user_id=user_id, home_organization_id=None, base_role_id=uuid4(),
        base_permissions=frozenset(), is_external=False,
    ))


def _job(owner):
    return PlatformJob(
        requested_by_user_id=str(owner), organization_id=None, job_type="solution.deploy",
        payload={}, requested_by_email="owner@example.com", requested_by_name="Owner",
        title="Deploy", status="queued",
    )


def test_platform_job_owner_needs_no_named_grant(monkeypatch):
    owner = uuid4()
    monkeypatch.setattr(
        "src.services.platform_job_access.require_named_permission",
        lambda *args: pytest.fail("owner must not need a named grant"),
    )
    require_platform_job_access(_caller(owner), _job(owner))


def test_other_job_records_the_actual_scoped_read_permission(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.services.platform_job_access.require_named_permission",
        lambda caller, permission, organization_id: calls.append((permission, organization_id)),
    )
    require_platform_job_access(_caller(uuid4()), _job(uuid4()))
    assert calls == [("platformjobs.read.all", None)]


def test_other_job_refusal_is_privacy_denial(monkeypatch):
    from src.services.authorization.enforce import PermissionDenied

    def deny(*_args):
        raise PermissionDenied("platformjobs.read.all")

    monkeypatch.setattr("src.services.platform_job_access.require_named_permission", deny)
    with pytest.raises(PlatformJobAccessDenied):
        require_platform_job_access(_caller(uuid4()), _job(uuid4()))


def test_other_organization_job_is_refused_before_named_permission(monkeypatch):
    def unexpected(*_args):
        pytest.fail("out-of-reach rows must not evaluate a named grant")

    monkeypatch.setattr("src.services.platform_job_access.require_named_permission", unexpected)
    job = _job(uuid4())
    job.organization_id = uuid4()
    with pytest.raises(PlatformJobAccessDenied):
        require_platform_job_access(_caller(uuid4()), job)
