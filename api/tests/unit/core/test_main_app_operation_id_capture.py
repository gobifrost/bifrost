"""R2a-2 (post-review rework): the scope-based operation_id capture, driven
through the exact wiring ``src.main.create_app()`` installs on the real
main API app.

Mirrors tests/unit/services/test_worker_sdk_http.py::TestOperationIdCapture,
which proves the same thing for the worker-local SDK socket app. The
mechanism (emit_audit reading ``request.scope["route"]`` live via the scope
dict the request-context middleware stashes) needs no per-route or per-app
wiring, so it works identically regardless of whether a route reached the
app through ``include_router`` (main app) or was spliced directly into
``app.router.routes`` (worker-local SDK app) — these two tests are the
proof for each half.

Builds a fresh, isolated FastAPI app using the exact same
``register_exception_handlers`` / ``install_request_context_middleware``
wiring the real ``src.main.create_app()`` installs, rather than mutating
the real ``src.main.app`` singleton — that singleton is shared process-wide
and inspected by other tests (e.g. test_operation_inventory.py scans its
live route table against the canonical catalog), so adding ad-hoc routes to
it directly would leak test-only operation ids into that inventory.
"""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI


def _wired_test_app() -> FastAPI:
    from src.core.app_wiring import (
        install_request_context_middleware,
        register_exception_handlers,
    )

    app = FastAPI()
    register_exception_handlers(app)
    install_request_context_middleware(app)
    return app


class TestMainAppWiringOperationIdCapture:
    @pytest.mark.asyncio
    async def test_catalogued_route_records_operation_id(self, async_session_factory):
        from sqlalchemy import delete, select

        from src.core.database import get_db_context
        from src.models.orm.audit import AuditLog
        from src.services.audit import emit_audit

        app = _wired_test_app()
        action = f"__test__.main.catalogued.{uuid4().hex[:8]}"

        @app.get("/main-catalogued-op", operation_id="__test__.main_catalogued_op")
        async def _catalogued():
            async with get_db_context() as db:
                await emit_audit(db, action)
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/main-catalogued-op")
            assert response.status_code == 200, response.text

            async with async_session_factory() as session:
                row = (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == action)
                    )
                ).scalar_one()
            assert row.operation_id == "__test__.main_catalogued_op"
        finally:
            async with async_session_factory() as cleanup:
                await cleanup.execute(delete(AuditLog).where(AuditLog.action == action))
                await cleanup.commit()

    @pytest.mark.asyncio
    async def test_uncatalogued_route_records_none(self, async_session_factory):
        from sqlalchemy import delete, select

        from src.core.database import get_db_context
        from src.models.orm.audit import AuditLog
        from src.services.audit import emit_audit

        app = _wired_test_app()
        action = f"__test__.main.uncatalogued.{uuid4().hex[:8]}"

        @app.get("/main-uncatalogued-op")
        async def _uncatalogued():
            async with get_db_context() as db:
                await emit_audit(db, action)
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/main-uncatalogued-op")
            assert response.status_code == 200, response.text

            async with async_session_factory() as session:
                row = (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == action)
                    )
                ).scalar_one()
            assert row.operation_id is None
        finally:
            async with async_session_factory() as cleanup:
                await cleanup.execute(delete(AuditLog).where(AuditLog.action == action))
                await cleanup.commit()
