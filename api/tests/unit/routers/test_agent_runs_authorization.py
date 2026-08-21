"""Authorization boundaries for agent run administration routes."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import AgentRun
from src.routers import agent_runs
from src.services.execution.agent_run_access import (
    agent_run_visibility_conditions_for_authorization,
)
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationBoundaryKind,
    AuthorizationContext,
)


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "jobs-operator@example.com",
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        name="Jobs Operator",
        organization_id=None,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary or AuthorizationBoundary.platform(),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def _compile_agent_run_visibility_sql(
    user: UserPrincipal,
    authorization: AuthorizationContext,
) -> str:
    stmt = select(AgentRun.id).where(
        *agent_run_visibility_conditions_for_authorization(user, authorization)
    )
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_agent_run_platform_job_helpers_require_platform_boundary() -> None:
    authorization = _authorization(
        capabilities={"platformjobs.read"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        agent_runs._require_agent_run_platform_job(
            authorization,
            "platformjobs.read",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


def test_agent_run_platform_job_helpers_require_declared_capability() -> None:
    authorization = _authorization(capabilities={"platformjobs.read"})

    with pytest.raises(HTTPException) as exc:
        agent_runs._require_agent_run_platform_job(
            authorization,
            "platformjobs.execute",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: platformjobs.execute"


def test_agent_run_visibility_keeps_own_runs_without_read_capability() -> None:
    user = UserPrincipal(
        user_id=uuid4(),
        email="owner@example.com",
        organization_id=uuid4(),
    )
    authorization = _authorization(
        capabilities=set(),
        boundary=AuthorizationBoundary.organization(user.organization_id),
    )

    sql = _compile_agent_run_visibility_sql(user, authorization)

    assert f"agent_runs.caller_user_id = '{user.user_id}'" in sql
    assert "agent_runs.org_id =" not in sql


def test_agent_run_visibility_exact_org_requires_selected_org() -> None:
    org_id = uuid4()
    user = UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        organization_id=uuid4(),
    )
    authorization = _authorization(
        capabilities={"executions.read"},
        boundary=AuthorizationBoundary.organization(org_id),
    )

    sql = _compile_agent_run_visibility_sql(user, authorization)

    assert f"agent_runs.org_id = '{org_id}'" in sql
    assert f"agent_runs.org_id = '{user.organization_id}'" not in sql
    assert f"agent_runs.caller_user_id = '{user.user_id}'" in sql


def test_agent_run_visibility_platform_boundary_is_global_only() -> None:
    user = UserPrincipal(
        user_id=uuid4(),
        email="platform-builder@example.com",
        organization_id=uuid4(),
    )
    authorization = _authorization(
        capabilities={"executions.read"},
        boundary=AuthorizationBoundary.platform(),
    )

    sql = _compile_agent_run_visibility_sql(user, authorization)

    assert "agent_runs.org_id IS NULL" in sql
    assert f"agent_runs.org_id = '{user.organization_id}'" not in sql


def test_agent_run_visibility_managed_boundary_uses_customer_orgs() -> None:
    user = UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        organization_id=uuid4(),
    )
    authorization = _authorization(
        capabilities={"executions.read"},
        boundary=AuthorizationBoundary(AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS),
    )

    sql = _compile_agent_run_visibility_sql(user, authorization)

    assert "organizations.is_provider IS false" in sql
    assert "agent_runs.org_id IN" in sql


def test_agent_run_visibility_platform_superuser_is_wildcard() -> None:
    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=uuid4(),
    )
    authorization = _authorization(
        capabilities={"platform.superuser"},
        boundary=AuthorizationBoundary.platform(),
    )

    sql = _compile_agent_run_visibility_sql(user, authorization)

    assert "agent_runs.org_id =" not in sql
    assert "agent_runs.org_id IS NULL" not in sql
