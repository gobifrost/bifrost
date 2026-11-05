"""Regression coverage for the router package import boundary."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[2]


def _run_probe(source: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(completed.stdout)


def test_worker_sdk_app_avoids_unrelated_router_and_provider_imports() -> None:
    """The SDK socket mounts its routes without importing unrelated API graphs."""
    result = _run_probe(
        """
        import json
        import sys

        from fastapi.routing import APIRoute
        from src.routers import cli
        from src.services.execution.worker_sdk_http import (
            AGENT_RUN_ROUTE_METHODS,
            AI_ROUTE_PATHS,
            ARTIFACT_ROUTE_PATHS,
            CONFIG_ROUTE_PATHS,
            EVENT_ROUTE_METHODS,
            EXECUTION_ROUTE_METHODS,
            FILES_ROUTE_PATHS,
            FORM_ROUTE_METHODS,
            INTEGRATION_ROUTE_PATHS,
            KNOWLEDGE_ROUTE_PATHS,
            ORGANIZATION_ROUTE_METHODS,
            PLATFORM_JOB_ROUTE_METHODS,
            ROLES_ROUTE_METHODS,
            SDK_CONTEXT_ROUTE_METHODS,
            SDK_MODULES_ROUTE_METHODS,
            SDK_RUNTIME_ROUTE_METHODS,
            TABLE_ROUTE_METHODS,
            TABLE_SDK_ROUTE_PATHS,
            USER_ROUTE_METHODS,
            WORKFLOW_ROUTE_METHODS,
            build_worker_sdk_app,
        )

        app = build_worker_sdk_app()
        expected_paths = set().union(
            ARTIFACT_ROUTE_PATHS,
            CONFIG_ROUTE_PATHS,
            INTEGRATION_ROUTE_PATHS,
            TABLE_SDK_ROUTE_PATHS,
            FILES_ROUTE_PATHS,
            KNOWLEDGE_ROUTE_PATHS,
            AI_ROUTE_PATHS,
            TABLE_ROUTE_METHODS,
            PLATFORM_JOB_ROUTE_METHODS,
            WORKFLOW_ROUTE_METHODS,
            EXECUTION_ROUTE_METHODS,
            AGENT_RUN_ROUTE_METHODS,
            EVENT_ROUTE_METHODS,
            FORM_ROUTE_METHODS,
            ORGANIZATION_ROUTE_METHODS,
            USER_ROUTE_METHODS,
            ROLES_ROUTE_METHODS,
            SDK_CONTEXT_ROUTE_METHODS,
            SDK_MODULES_ROUTE_METHODS,
            SDK_RUNTIME_ROUTE_METHODS,
        )
        mounted_paths = {
            route.path
            for route in app.router.routes
            if isinstance(route, APIRoute)
        }
        unrelated = sorted(
            module
            for module in sys.modules
            if module in {
                "src.routers.agent_runs",
                "src.routers.mcp",
                "src.routers.mcp_servers",
                "src.routers.mcp_connections",
                "src.routers.mcp_oauth_callback",
            }
            or module.startswith("src.services.mcp_server.")
        )
        provider_sdks = sorted(
            module
            for module in sys.modules
            if any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in {
                    "anthropic",
                    "google.genai",
                    "openai",
                    "pydantic_ai",
                }
            )
        )

        print(json.dumps({
            "cli_module_name": cli.__name__,
            "mounted_paths": sorted(mounted_paths),
            "expected_paths": sorted(expected_paths),
            "unrelated": unrelated,
            "provider_sdks": provider_sdks,
        }))
        """
    )

    assert result["cli_module_name"] == "src.routers.cli"
    assert result["mounted_paths"] == result["expected_paths"]
    assert result["unrelated"] == []
    assert result["provider_sdks"] == []


def test_worker_sdk_form_and_user_routes_defer_non_sdk_services() -> None:
    """SDK CRUD route import does not load browser or admin-only services."""
    result = _run_probe(
        """
        import json
        import sys

        import src.routers.forms
        import src.routers.users

        deferred = {
            "shared.form_captcha",
            "shared.form_execution",
            "shared.form_provider",
            "shared.form_publication",
            "shared.run_lineage",
            "src.repositories.workflows",
            "src.services.access_explain",
            "src.services.events",
            "src.services.user_access_map",
            "src.services.user_invite_service",
            "src.services.user_mfa_reset",
            "src.services.user_role_assignments",
        }
        loaded = sorted(module for module in deferred if module in sys.modules)
        print(json.dumps({"loaded": loaded}))
        """
    )

    assert result["loaded"] == []
