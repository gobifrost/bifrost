"""HTTP and worker SDK surfaces map shared authorization refusals identically."""

import httpx
import pytest
from fastapi import FastAPI

from src.services.authorization.enforce import PermissionDenied


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["api", "worker"])
async def test_shared_named_permission_refusal_is_http_403(surface):
    if surface == "worker":
        from src.services.execution.worker_sdk_http import build_worker_sdk_app

        app = build_worker_sdk_app()
    else:
        from src.main import app as main_app

        # Use the handler installed on the real API without mutating its routes.
        app = FastAPI()
        app.add_exception_handler(PermissionDenied, main_app.exception_handlers[PermissionDenied])

    error = PermissionDenied("settings.readwrite")

    @app.get("/__test__/permission-denied")
    async def denied():
        raise error

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bifrost.test"
    ) as client:
        response = await client.get("/__test__/permission-denied")

    assert response.status_code == 403
    assert response.json() == {"detail": error.detail}
