"""Authorization callers for tests of evaluator-gated services and handlers."""

from __future__ import annotations

import importlib
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, WILDCARD_PERMISSION
from src.core.principal import UserPrincipal
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller


def platform_admin_grant() -> RoleGrant:
    """The Platform Admin assignment: an additional role at the platform
    boundary, never a base role."""
    return RoleGrant(
        PLATFORM_ADMIN_ROLE_ID,
        frozenset({WILDCARD_PERMISSION}),
        (Boundary(BoundaryKind.PLATFORM),),
    )


def admin_caller(email: str = "admin@example.com", user_id: UUID | None = None) -> Caller:
    """A Platform Admin caller, its context built in memory as
    ``load_caller`` would read it for an admin."""
    principal = UserPrincipal(
        user_id=user_id or uuid4(),
        email=email,
        organization_id=None,
        name="Admin",
        is_superuser=True,
        is_verified=True,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=None,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(platform_admin_grant(),),
        ),
    )


@contextmanager
def handlers_as(caller: Caller, module: str):
    """Make the handlers in ``module`` load ``caller`` instead of reading
    the database, for router-boundary tests with a mocked session."""
    with ExitStack() as stack:
        for name in ("load_caller", "authorize_operation"):
            if hasattr(importlib.import_module(module), name):
                stack.enter_context(patch(f"{module}.{name}", new=AsyncMock(return_value=caller)))
        yield caller
