"""A transaction PostgreSQL aborts to break a deadlock answers with a clear,
explicit error instead of a generic 500."""

from __future__ import annotations

import asyncio
import secrets

import asyncpg
import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.database_retry import is_transaction_conflict
from src.core.transaction_conflicts import conflict_aware_route

DEADLOCK = DBAPIError("UPDATE", {}, asyncpg.exceptions.DeadlockDetectedError("deadlock detected"))


@pytest.mark.parametrize(
    ("exc", "conflict"),
    [
        (DEADLOCK, True),
        (DBAPIError("UPDATE", {}, asyncpg.exceptions.SerializationError("could not serialize")), True),
        (asyncpg.exceptions.DeadlockDetectedError("deadlock detected"), True),
        (IntegrityError("INSERT", {}, asyncpg.exceptions.UniqueViolationError("dup")), False),
        (DBAPIError("SELECT 1", {}, asyncpg.exceptions.AdminShutdownError("shutdown")), False),
        (ValueError("bad"), False),
    ],
)
def test_only_an_aborted_conflict_is_a_transaction_conflict(exc, conflict):
    assert is_transaction_conflict(exc) is conflict


@pytest.mark.asyncio
async def test_a_real_deadlock_is_recognized(async_session_factory):
    """Two transactions take two locks in opposite order; PostgreSQL aborts
    one, and the error the driver raises is classified as a conflict."""
    first, second = secrets.randbits(62), secrets.randbits(62)
    holding = [asyncio.Event(), asyncio.Event()]

    async def transaction(index: int, keys: tuple[int, int]) -> None:
        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": keys[0]})
                holding[index].set()
                await holding[1 - index].wait()
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": keys[1]})

    async with asyncio.timeout(30):
        results = await asyncio.gather(
            transaction(0, (first, second)),
            transaction(1, (second, first)),
            return_exceptions=True,
        )

    errors = [result for result in results if isinstance(result, BaseException)]
    assert len(errors) == 1
    assert is_transaction_conflict(errors[0])


def _app(raised: Exception) -> FastAPI:
    router = APIRouter(
        route_class=conflict_aware_route(lambda: HTTPException(409, "Nothing was saved; try again."))
    )

    @router.post("/change")
    async def change() -> None:
        raise raised

    app = FastAPI()
    app.include_router(router)
    return app


async def _post(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/change")


@pytest.mark.asyncio
async def test_a_conflict_becomes_the_routes_explicit_error():
    response = await _post(_app(DEADLOCK))

    assert response.status_code == 409
    assert response.json() == {"detail": "Nothing was saved; try again."}


@pytest.mark.asyncio
async def test_other_errors_pass_through_unchanged():
    response = await _post(_app(IntegrityError("INSERT", {}, Exception("dup"))))

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_a_scim_conflict_asks_the_identity_provider_to_retry():
    from src.routers.scim import _conflict, scim_error_response
    from src.services.identity_provisioning.scim import ScimError

    router = APIRouter(route_class=conflict_aware_route(_conflict))

    @router.post("/change")
    async def change() -> None:
        raise DEADLOCK

    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ScimError, lambda _request, exc: scim_error_response(exc))  # type: ignore[arg-type]

    response = await _post(app)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    body = response.json()
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert body["status"] == "503"
