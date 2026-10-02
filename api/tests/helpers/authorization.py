"""Authorization callers for tests of evaluator-gated services and handlers."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


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
            base_role_id=PLATFORM_ADMIN_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
        ),
    )


@contextmanager
def handlers_as(caller: Caller, module: str):
    """Make the handlers in ``module`` load ``caller`` instead of reading
    the database, for router-boundary tests with a mocked session."""
    loaded = AsyncMock(return_value=caller)
    with patch(f"{module}.load_caller", new=loaded):
        if hasattr(__import__(module, fromlist=["_"]), "authorize_operation"):
            with patch(f"{module}.authorize_operation", new=AsyncMock(return_value=caller)):
                yield caller
        else:
            yield caller
