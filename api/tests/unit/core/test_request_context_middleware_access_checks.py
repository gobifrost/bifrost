"""The request middleware judges a request's access checks after the response."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from shared import access_checks
from src.core.app_wiring import install_request_context_middleware
from src.core.principal import UserPrincipal
from src.core.security import create_access_token
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import RuntimeAuthority


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _session_factory():
    return _Session


def _runtime_credential() -> tuple[str, UserPrincipal]:
    run_user_id, actor_id, execution_id, workflow_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    context = AuthorizationContext(
        user_id=run_user_id,
        home_organization_id=None,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset(),
    )
    principal = UserPrincipal(
        user_id=actor_id,
        email="actor@example.test",
        organization_id=None,
        is_verified=True,
    )
    principal.runtime_authority = RuntimeAuthority(
        kind="workflow_execution",
        credential_id=execution_id,
        run_user=RunUser(run_user_id, context, None, is_active=True),
        powers=Powers(WorkflowPermissionMode.FULL, ()),
        acting_user_id=actor_id,
        organization_id=None,
        workflow_id=workflow_id,
        started_by_user_id=run_user_id,
        root_execution_id=execution_id,
    )
    return (
        create_access_token(
            {"sub": str(execution_id), "credential_kind": "workflow_execution"}
        ),
        principal,
    )


def _install_runtime_resolver(
    mp: pytest.MonkeyPatch, principal: UserPrincipal | None
) -> AsyncMock:
    resolver = AsyncMock(return_value=principal)
    mp.setattr("src.core.database.get_session_factory", _session_factory)
    mp.setattr("src.services.authorization.runtime_auth.load_runtime_principal", resolver)
    return resolver


def _app() -> FastAPI:
    app = FastAPI()
    install_request_context_middleware(app)

    @app.get("/__test__/checked/{name}")
    async def _checked(name: str):
        access_checks.note("scope_switch", None, name=name)
        return {"ok": True}

    @app.get("/__test__/power")
    async def _power():
        access_checks.note("secret", None, kind="config", name="k")
        access_checks.note_power("agents.read", None, subject="agent:1")
        return {"ok": True}

    @app.get("/__test__/secret")
    async def _secret():
        access_checks.note("secret", None, kind="config", name="k")
        return {"ok": True}

    @app.get("/__test__/refused")
    async def _refused():
        access_checks.note("scope_switch", None)
        raise HTTPException(status_code=403, detail="Access denied")

    return app


async def _get(
    path: str,
    token: str,
    flushed: list,
    *,
    runtime_principal: UserPrincipal | None = None,
) -> httpx.Response:
    async def capture(db, collector, *, operation, route):
        flushed.append((collector, route, operation))

    transport = httpx.ASGITransport(app=_app())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.services.access_check_writer.flush", capture)
        _install_runtime_resolver(mp, runtime_principal)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers={"Authorization": f"Bearer {token}"})


async def test_a_verified_runtime_request_is_judged_with_its_route() -> None:
    flushed: list = []
    token, principal = _runtime_credential()

    response = await _get(
        "/__test__/checked/a", token, flushed, runtime_principal=principal
    )

    assert response.status_code == 200
    [(collector, route, operation)] = flushed
    assert (route, operation) == (
        ("GET", "/__test__/checked/{name}"),
        "GET /__test__/checked/{name}",
    )
    assert [note.facts for note in collector.notes] == [{"name": "a"}]


async def test_a_refused_request_keeps_its_response_and_is_still_judged() -> None:
    flushed: list = []
    token, principal = _runtime_credential()

    response = await _get(
        "/__test__/refused", token, flushed, runtime_principal=principal
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Access denied"}
    assert len(flushed) == 1


def _person_token(person: str) -> str:
    return create_access_token(
        {"sub": person, "email": "p@x.example", "org_id": str(uuid4())}
    )


async def test_a_persons_request_noting_only_run_kinds_is_not_judged() -> None:
    flushed: list = []

    response = await _get("/__test__/secret", _person_token(str(uuid4())), flushed)

    assert response.status_code == 200
    assert flushed == []


async def test_a_persons_named_permission_is_judged_as_theirs() -> None:
    flushed: list = []
    person = str(uuid4())

    response = await _get("/__test__/power", _person_token(person), flushed)

    assert response.status_code == 200
    [(collector, _route, _operation)] = flushed
    assert (collector.direct, str(collector.run_user_id)) == (True, person)
    assert [note.kind for note in collector.notes] == ["permission"]


async def test_a_failing_writer_never_changes_the_response() -> None:
    async def broken(db, collector, *, operation, route):
        raise RuntimeError("boom")

    token, principal = _runtime_credential()
    transport = httpx.ASGITransport(app=_app())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.services.access_check_writer.flush", broken)
        _install_runtime_resolver(mp, principal)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/__test__/checked/a", headers={"Authorization": f"Bearer {token}"}
            )

    assert response.status_code == 200


async def test_an_unresolved_typed_credential_skips_checks_not_the_request() -> None:
    flushed: list = []
    token, _ = _runtime_credential()

    response = await _get("/__test__/checked/a", token, flushed)

    assert response.status_code == 200
    assert flushed == []


async def test_checks_are_judged_after_the_whole_response_is_sent() -> None:
    """The client gets the full response before the checks are judged and
    written, so judging never adds latency to the request."""
    timeline: list[str] = []
    app = _app()
    token, principal = _runtime_credential()

    async def recording_app(scope, receive, send):
        async def recording_send(message):
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                timeline.append("response sent")
            await send(message)

        await app(scope, receive, recording_send)

    async def capture(db, collector, *, operation, route):
        timeline.append("judged")

    transport = httpx.ASGITransport(app=recording_app)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.services.access_check_writer.flush", capture)
        _install_runtime_resolver(mp, principal)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/__test__/checked/a", headers={"Authorization": f"Bearer {token}"}
            )

    assert response.status_code == 200
    assert timeline == ["response sent", "judged"]
