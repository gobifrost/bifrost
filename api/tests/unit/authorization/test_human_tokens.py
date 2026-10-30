"""Human claims preserve decisions and reject changes before handler effects."""

from uuid import uuid4

import pytest
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.auth import get_current_user, REFRESH_REQUIRED_CHALLENGE
from src.core.principal import UserPrincipal
from src.core.security import decode_token
from src.models.orm.users import User
from src.services.authorization.context import build_authorization_context
from src.services.authorization.enforce import load_caller
from src.services.authorization.tokens import (
    HumanSubject, TokenSubjectUnavailable, authenticate_human_token, issue_access_token,
    parse_human_claims, parse_runtime_claims,
)
from tests.unit.authorization.committed_runs import committed_world


async def test_human_claims_reproduce_context_and_epoch_invalidates_changed_grants(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        org = await world.organization(provider=True)
        human = await world.person(org, external=True)
        role = await world.role("workflows.read", "tables.read")
        await world.assign(human, role.id, ("organization", org.id))
        await world.session.commit()
        issued = await issue_access_token(HumanSubject(human.id, uuid4()))
        payload = decode_token(issued.token, expected_type="access")
        assert payload is not None and isinstance(payload["usr"], str)
        assert parse_runtime_claims(payload) is None
        assert parse_human_claims(payload) == issued.claims
        async with AsyncSession(async_engine) as db:
            result = await authenticate_human_token(db, issued.claims)
            assert result.principal is not None
            principal = result.principal
            assert principal.is_external and principal.is_provider_org
            assert principal.role_ids == [role.id]
            assert (await load_caller(db, principal)).ctx == await build_authorization_context(db, human.id)
        human.name = "Changed after mint"
        await world.session.commit()
        async with AsyncSession(async_engine) as db:
            assert (await authenticate_human_token(db, issued.claims)).refresh_required


@pytest.mark.parametrize("field,value", [("is_active", False), ("is_system", True), ("identity_kind", "custom")])
async def test_ineligible_accounts_cannot_mint_human_credentials(async_engine: AsyncEngine, field: str, value: object) -> None:
    async with committed_world(async_engine) as world:
        human = await world.person(await world.organization())
        setattr(human, field, value)
        await world.session.commit()
        with pytest.raises(TokenSubjectUnavailable):
            await issue_access_token(HumanSubject(human.id))


async def test_human_epoch_challenge_precedes_mutation(async_engine: AsyncEngine) -> None:
    effects: list[str] = []
    app = FastAPI()

    @app.post("/mutation")
    async def mutate(user: UserPrincipal = Depends(get_current_user)) -> dict[str, str]:
        effects.append(str(user.user_id))
        return {"status": "ok"}

    async with committed_world(async_engine) as world:
        human = await world.person(await world.organization())
        await world.session.commit()
        issued = await issue_access_token(HumanSubject(human.id))
        human.name = "New name"
        await world.session.commit()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            refused = await client.post("/mutation", headers={"Authorization": f"Bearer {issued.token}"})
            assert refused.status_code == 401
            assert refused.headers["WWW-Authenticate"] == REFRESH_REQUIRED_CHALLENGE
            assert effects == []
            fresh = await issue_access_token(HumanSubject(human.id))
            accepted = await client.post("/mutation", headers={"Authorization": f"Bearer {fresh.token}"})
            assert accepted.status_code == 200 and effects == [str(human.id)]


async def test_human_schema_refuses_hybrid_runtime_embed_and_mismatched_subject(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        human = await world.person(await world.organization())
        await world.session.commit()
        claims = (await issue_access_token(HumanSubject(human.id))).claims
        for changes in ({"run": {}}, {"credential_kind": "agent_run"}, {"embed": True}, {"sub": str(uuid4())}):
            assert parse_human_claims({**claims.to_payload(), **changes}) is None


async def test_system_flag_change_is_epoch_covered(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        human = await world.person(await world.organization())
        await world.session.commit()
        issued = await issue_access_token(HumanSubject(human.id))
        human.is_system = True
        await world.session.commit()
        async with AsyncSession(async_engine) as db:
            assert (await authenticate_human_token(db, issued.claims)).refresh_required
            assert (await db.execute(select(User.is_system).where(User.id == human.id))).scalar_one() is True


async def test_password_change_revokes_every_refresh_family_and_prior_access(async_engine: AsyncEngine) -> None:
    from shared.human_sessions import HumanSessionUnavailable, issue_human_session, rotate_human_session
    from src.core.security import get_password_hash
    from src.models import PasswordChange
    from src.routers.profile import change_password

    async with committed_world(async_engine) as world:
        human = await world.person(await world.organization())
        human.hashed_password = get_password_hash("Original-password-42!")
        await world.session.commit()
        tokens = [await issue_human_session(human.id) for _ in range(2)]
        payload = decode_token(tokens[0].access_token, expected_type="access")
        assert payload is not None
        claims = parse_human_claims(payload)
        assert claims is not None
        async with AsyncSession(async_engine) as db:
            principal = (await authenticate_human_token(db, claims)).principal
        assert principal is not None
        await change_password(
            PasswordChange(current_password="Original-password-42!", new_password="New-password-42!"),
            principal, world.session,
        )
        for token in tokens:
            with pytest.raises(HumanSessionUnavailable):
                await rotate_human_session(token.refresh_token)
        async with AsyncSession(async_engine) as db:
            assert (await authenticate_human_token(db, claims)).refresh_required
