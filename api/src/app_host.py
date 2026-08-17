"""Narrow ASGI sub-application for isolated Solution apps."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from src.core.auth import get_current_active_user, get_execution_context
from src.routers.solution_app_host import router as solution_app_host_router
from src.routers.solution_app_runtime import (
    get_solution_app_execution_context,
    get_solution_app_user,
    router as solution_app_runtime_router,
)
from src.routers.solution_app_websocket import router as solution_app_websocket_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Bifrost Isolated App Runtime",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # CSP-sandboxed documents have an opaque ``Origin: null`` even though the
    # requested URL is on the Bifrost host. Admit only that origin to this
    # deliberately narrow SDK surface.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["null"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.dependency_overrides[get_current_active_user] = get_solution_app_user
    app.dependency_overrides[get_execution_context] = get_solution_app_execution_context
    app.include_router(solution_app_runtime_router)
    app.include_router(solution_app_host_router)
    app.include_router(solution_app_websocket_router)
    return app
