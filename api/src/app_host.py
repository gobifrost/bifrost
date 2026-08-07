"""Standalone app-origin ASGI app for generated Solution previews."""

from __future__ import annotations

from fastapi import FastAPI

from src.core.auth import get_current_active_user, get_execution_context
from src.routers.solution_app_host import router as solution_app_host_router
from src.routers.solution_app_runtime import (
    get_solution_app_execution_context,
    get_solution_app_user,
    router as solution_app_runtime_router,
)
from src.routers.solution_app_websocket import router as solution_app_websocket_router


def create_app() -> FastAPI:
    app = FastAPI(title="Bifrost App Host", docs_url=None, redoc_url=None)
    app.dependency_overrides[get_current_active_user] = get_solution_app_user
    app.dependency_overrides[get_execution_context] = (
        get_solution_app_execution_context
    )
    app.include_router(solution_app_runtime_router)
    app.include_router(solution_app_host_router)
    app.include_router(solution_app_websocket_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.app_host:app", host="0.0.0.0", port=8100, reload=False)
