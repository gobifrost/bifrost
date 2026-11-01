"""Signed human claims fixtures for transport tests (no database oracle)."""

import time
from uuid import UUID, uuid4

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID
from src.config import get_settings
from src.services.authorization.tokens import BaseRoleClaim, HumanAccessTokenClaims, RoleClaim, UserClaims


def human_claims(
    *, user_id: UUID | None = None, organization_id: UUID | None = None,
    admin: bool = False, external: bool = False, provider: bool = False,
    epoch: int = 100,
) -> HumanAccessTokenClaims:
    settings = get_settings()
    user_id = user_id or uuid4()
    now = int(time.time())
    return HumanAccessTokenClaims(
        iss=settings.jwt_issuer, aud=settings.jwt_audience, iat=now, exp=now + 4500,
        jti=str(uuid4()), av=str(epoch), sub=user_id, sid=uuid4(),
        usr=UserClaims(
            id=user_id, email="human@example.test", name="Human", org=organization_id,
            ext=external, prov=provider, active=True,
            base=BaseRoleClaim(id=USER_ROLE_ID, perm=["workflows.read"]),
            roles=[RoleClaim(id=PLATFORM_ADMIN_ROLE_ID, name="Platform Admin", perm=["*"], bnd=[("platform", None)])]
            if admin else [],
        ),
    )
