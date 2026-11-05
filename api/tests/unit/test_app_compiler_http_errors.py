"""Compiler errors retain their public meaning through shared API wiring."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.app_wiring import register_exception_handlers
from src.services.app_compiler import AppCompilerInfrastructureError
from src.services.app_resource_limits import AppSourceLimitError
from src.services.node_subprocess import NodeScriptAdmissionError


@pytest.mark.parametrize(
    "failure,status,message",
    [
        (AppSourceLimitError("Split or reduce the file and retry."), 422,
         "Split or reduce the file and retry."),
        (NodeScriptAdmissionError("App compiler is busy; retry after other App builds finish."),
         422, "App compiler is busy; retry after other App builds finish."),
        (AppCompilerInfrastructureError("private compiler stderr"), 503,
         "App compilation is temporarily unavailable. Retry the operation."),
    ],
)
def test_compiler_http_error_contract(failure, status, message):
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/compile")
    async def compile_source():
        raise failure

    with TestClient(app) as client:
        response = client.get("/compile")
    assert response.status_code == status
    assert response.json()["message"] == message
    assert "private compiler stderr" not in response.text
