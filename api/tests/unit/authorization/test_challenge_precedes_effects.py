"""The refresh challenge comes from the auth dependency, before any effect.

A client replays a request only after the ``refresh_required`` challenge, so
the challenge must be raised before anything with an effect has run: the
route handler, and every dependency resolved after authentication. These
tests walk the real application's dependency graph and drive a mutation
route with expired and valid tokens.
"""

# No ``from __future__ import annotations``: FastAPI must resolve these
# routes' Annotated dependencies, which name functions local to each test.
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Annotated, Any
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from src.config import get_settings
from src.core.auth import REFRESH_REQUIRED_CHALLENGE, CurrentUser, bearer_scheme, get_current_user
from src.core.database import get_db
from src.core.security import (
    create_access_token,
    create_embed_access_token,
    create_refresh_token,
    decode_token,
)

# Dependencies that may run before authentication: opening (not using) a
# database session, reading the bearer header, reading settings.
_SIDE_EFFECT_FREE: tuple[object, ...] = (get_db, bearer_scheme, get_settings)


def _resolution_order(dependant: Dependant) -> list[Callable[..., Any]]:
    """Dependency calls in the order FastAPI resolves them: depth first,
    sub-dependencies before their dependant, cached calls once."""
    order: list[Callable[..., Any]] = []
    seen: set[int] = set()

    def walk(node: Dependant) -> None:
        for sub in node.dependencies:
            walk(sub)
            if sub.call is None or (sub.use_cache and id(sub.call) in seen):
                continue
            seen.add(id(sub.call))
            order.append(sub.call)

    walk(dependant)
    return order


def _effects_before_authentication(route: APIRoute) -> list[Callable[..., Any]] | None:
    """Calls resolved before ``get_current_user`` that are not known to be
    side-effect free; None when the route does not authenticate with it."""
    order = _resolution_order(route.dependant)
    if not any(call is get_current_user for call in order):
        return None
    index = next(i for i, call in enumerate(order) if call is get_current_user)
    return [call for call in order[:index] if not any(call is allowed for allowed in _SIDE_EFFECT_FREE)]


def test_every_authenticated_route_runs_only_side_effect_free_dependencies_first() -> None:
    from src.main import app

    authenticated = 0
    offenders: dict[str, list[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        before = _effects_before_authentication(route)
        if before is None:
            continue
        authenticated += 1
        if before:
            offenders[f"{sorted(route.methods or ())} {route.path}"] = [
                getattr(call, "__qualname__", repr(call)) for call in before
            ]

    assert authenticated > 100
    assert offenders == {}


def test_an_effectful_dependency_before_authentication_is_caught() -> None:
    async def write_something() -> None:
        return None

    router = APIRouter()

    @router.post("/probe")
    async def probe(_: Annotated[None, Depends(write_something)], user: CurrentUser) -> None:
        return None

    [route] = [r for r in router.routes if isinstance(r, APIRoute)]
    assert _effects_before_authentication(route) == [write_something]


# --- Driving a mutation route ------------------------------------------------


@pytest.fixture
def mutation_app() -> tuple[FastAPI, list[str]]:
    effects: list[str] = []

    async def no_database() -> AsyncIterator[None]:
        yield None

    async def later_dependency() -> None:
        effects.append("dependency")

    app = FastAPI()
    app.dependency_overrides[get_db] = no_database

    @app.post("/mutate")
    async def mutate(user: CurrentUser, _: Annotated[None, Depends(later_dependency)]) -> dict[str, str]:
        effects.append("handler")
        return {"user": str(user.user_id)}

    return app, effects


def _human_claims() -> dict[str, Any]:
    return {"sub": str(uuid4()), "email": "person@example.test", "org_id": str(uuid4()), "name": "Person"}


def _expired(claims: dict[str, Any]) -> str:
    return create_access_token(claims, expires_delta=timedelta(seconds=-30))


def _expired_runtime(claims: dict[str, Any]) -> str:
    """An expired, correctly signed runtime-shaped token. ``create_access_token``
    refuses runtime claims, so it is signed here directly."""
    settings = get_settings()
    payload = {
        **claims,
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": int(time.time()) - 30,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


async def _post(app: FastAPI, token: str | None) -> httpx.Response:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/mutate", headers=headers)


async def test_a_challenged_mutation_has_no_effect_and_its_replay_has_one(
    mutation_app: tuple[FastAPI, list[str]],
) -> None:
    app, effects = mutation_app
    claims = _human_claims()

    challenged = await _post(app, _expired(claims))

    assert challenged.status_code == 401
    assert challenged.headers["www-authenticate"] == REFRESH_REQUIRED_CHALLENGE
    assert effects == []

    replayed = await _post(app, create_access_token(claims))

    assert replayed.status_code == 200
    assert effects == ["dependency", "handler"]


def _wrong_signature() -> str:
    settings = get_settings()
    claims = {
        **_human_claims(),
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": 1,
    }
    return jwt.encode(claims, "not-the-secret-key-not-the-secret-key", algorithm=settings.algorithm)


@pytest.mark.parametrize(
    ("make_token", "challenged"),
    [
        (lambda: _expired(_human_claims()), True),
        (lambda: _expired_runtime({"sub": str(uuid4()), "credential_kind": "workflow_execution", "ver": 1}), True),
        (
            lambda: _expired_runtime(
                {"sub": str(uuid4()), "credential_kind": "service_attempt", "service_id": str(uuid4()), "ver": 1}
            ),
            True,
        ),
        # A legacy runtime token's refresh slot holds the same token.
        (lambda: _expired_runtime({"sub": str(uuid4()), "credential_kind": "workflow_execution"}), False),
        # Embed tokens and impersonation markers have no refresh.
        (
            lambda: create_embed_access_token(
                embed_kind="form", grant="public", resource_id=str(uuid4()), org_id=None,
                expires_delta=timedelta(seconds=-30),
            ),
            False,
        ),
        (
            lambda: _expired(
                {**_human_claims(), "impersonated_by_user_id": str(uuid4()), "impersonation_session_id": str(uuid4())}
            ),
            False,
        ),
        # Not signature-valid, or not an access token.
        (_wrong_signature, False),
        (lambda: create_refresh_token({"sub": str(uuid4())}, expires_delta=timedelta(seconds=-30))[0], False),
        (lambda: "not-a-jwt", False),
        (lambda: None, False),
    ],
    ids=[
        "expired person",
        "expired run token",
        "expired service run token",
        "expired legacy runtime token",
        "expired embed token",
        "expired impersonation marker",
        "wrong signature",
        "expired refresh token",
        "malformed",
        "no token",
    ],
)
async def test_only_a_refreshable_expired_access_token_gets_the_challenge(
    mutation_app: tuple[FastAPI, list[str]], make_token: Callable[[], str | None], challenged: bool
) -> None:
    app, effects = mutation_app

    response = await _post(app, make_token())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (REFRESH_REQUIRED_CHALLENGE if challenged else "Bearer")
    assert effects == []


def test_another_hosts_clock_skew_in_iat_never_rejects_a_token() -> None:
    settings = get_settings()
    future_iat = create_access_token({**_human_claims(), "iat": 4_102_444_800})

    payload = decode_token(future_iat, expected_type="access")

    assert payload is not None
    assert payload["iat"] == 4_102_444_800
    assert payload["iss"] == settings.jwt_issuer
