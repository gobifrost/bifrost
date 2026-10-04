"""The request middleware judges a run's access checks after the response."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi import FastAPI

from shared import access_checks
from src.core.app_wiring import install_request_context_middleware
from src.core.security import create_access_token, mint_engine_token


def _app() -> FastAPI:
    app = FastAPI()
    install_request_context_middleware(app)

    @app.get("/__test__/checked/{name}")
    async def _checked(name: str):
        access_checks.note("scope_switch", None, name=name)
        return {"ok": True}

    @app.get("/__test__/refused")
    async def _refused():
        access_checks.note("scope_switch", None)
        raise HTTPException(status_code=403, detail="Access denied")

    return app


def _engine_token() -> str:
    token, _ = mint_engine_token(
        execution_id=str(uuid4()),
        solution_id=None,
        global_repo_access=True,
        timeout_seconds=300,
        lineage={"run_user_id": str(uuid4()), "started_by_user_id": str(uuid4()), "root_execution_id": str(uuid4())},
    )
    return token


async def _get(path: str, token: str, flushed: list) -> httpx.Response:
    async def capture(db, collector, *, operation, route):
        flushed.append((collector, route, operation))

    transport = httpx.ASGITransport(app=_app())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.services.access_check_writer.flush", capture)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers={"Authorization": f"Bearer {token}"})


async def test_an_engine_request_is_judged_with_its_route() -> None:
    flushed: list = []

    response = await _get("/__test__/checked/a", _engine_token(), flushed)

    assert response.status_code == 200
    [(collector, route, operation)] = flushed
    assert (route, operation) == (("GET", "/__test__/checked/{name}"), "GET /__test__/checked/{name}")
    assert [note.facts for note in collector.notes] == [{"name": "a"}]


async def test_a_refused_request_keeps_its_response_and_is_still_judged() -> None:
    flushed: list = []

    response = await _get("/__test__/refused", _engine_token(), flushed)

    assert response.status_code == 403
    assert response.json() == {"detail": "Access denied"}
    assert len(flushed) == 1


async def test_a_persons_request_is_not_judged() -> None:
    flushed: list = []
    token = create_access_token({"sub": str(uuid4()), "email": "p@x.example", "org_id": str(uuid4())})

    response = await _get("/__test__/checked/a", token, flushed)

    assert response.status_code == 200
    assert flushed == []


async def test_a_failing_writer_never_changes_the_response() -> None:
    async def broken(db, collector, *, operation, route):
        raise RuntimeError("boom")

    transport = httpx.ASGITransport(app=_app())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.services.access_check_writer.flush", broken)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/__test__/checked/a", headers={"Authorization": f"Bearer {_engine_token()}"}
            )

    assert response.status_code == 200


async def test_unreadable_lineage_claims_skip_the_checks_not_the_request() -> None:
    flushed: list = []
    token, _ = mint_engine_token(
        execution_id=str(uuid4()),
        solution_id=None,
        global_repo_access=True,
        timeout_seconds=300,
        engine_workflow_id="not-a-uuid",
    )

    response = await _get("/__test__/checked/a", token, flushed)

    assert response.status_code == 200
    assert flushed == []


async def test_checks_are_judged_after_the_whole_response_is_sent() -> None:
    """The client gets the full response before the checks are judged and
    written, so judging never adds latency to the request."""
    timeline: list[str] = []
    app = _app()

    async def recording_app(scope, receive, send):
        async def recording_send(message):
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                timeline.append("response sent")
            await send(message)

        await app(scope, receive, recording_send)

    async def capture(db, collector, *, operation, route):
        timeline.append("judged")

    transport = httpx.ASGITransport(app=recording_app)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.services.access_check_writer.flush", capture)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/__test__/checked/a", headers={"Authorization": f"Bearer {_engine_token()}"}
            )

    assert response.status_code == 200
    assert timeline == ["response sent", "judged"]
