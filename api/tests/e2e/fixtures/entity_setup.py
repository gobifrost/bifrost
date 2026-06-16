"""
Entity creation fixtures for E2E testing.

Provides fixtures for creating workflows, apps, forms, and agents
with proper cleanup after tests.
"""

import logging
import time
from typing import Any, Generator

import pytest

from tests.e2e.conftest import write_and_register

logger = logging.getLogger(__name__)


@pytest.fixture
def test_workflow(e2e_client, platform_admin) -> Generator[dict[str, Any], None, None]:
    """
    Create a workflow via file editor for portable ref testing.

    The workflow is created by writing a Python file, which triggers
    the file indexer to discover and register the workflow.

    Uses unique timestamp in name to avoid conflicts between tests.

    Yields:
        dict with: id, name, file_path, portable_ref
    """
    # Use timestamp to ensure unique name/path per test to avoid conflicts
    unique_id = int(time.time() * 1000) % 1000000  # Last 6 digits of ms timestamp
    workflow_name = f"e2e_portable_{unique_id}"
    file_path = f"workflows/e2e_portable_{unique_id}.py"

    workflow_content = f'''"""Test workflow for portable ref E2E testing."""
from bifrost import workflow

@workflow(name="{workflow_name}", description="E2E portable ref test workflow")
def {workflow_name}(value: str) -> str:
    """Process a value for testing."""
    return f"processed: {{value}}"
'''

    # Create workflow file and register
    result = write_and_register(
        e2e_client, platform_admin.headers,
        file_path, workflow_content, workflow_name,
    )
    logger.info(f"Created and registered workflow: {file_path}")

    workflow_info = {
        "id": result["id"],
        "name": result["name"],
        "file_path": file_path,
        "portable_ref": f"{file_path}::{workflow_name}",
    }
    logger.info(f"Workflow registered: {workflow_info}")

    yield workflow_info

    # Cleanup: delete workflow file
    try:
        e2e_client.delete(
            f"/api/files/editor?path={file_path}",
            headers=platform_admin.headers,
        )
        logger.info(f"Cleaned up workflow file: {file_path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup workflow file: {e}")


@pytest.fixture
def test_app_with_workflow(
    e2e_client, platform_admin, test_workflow
) -> Generator[dict[str, Any], None, None]:
    """
    Create an application with components that reference a workflow.

    Creates:
    - App with one page
    - ButtonComponent with workflow_id
    - Page with launch_workflow_id

    Yields:
        dict with: app (full app data), workflow (workflow info)
    """
    # Create app
    app_response = e2e_client.post(
        "/api/applications",
        headers=platform_admin.headers,
        json={
            "name": "E2E Portable Ref Test App",
            "slug": "e2e-portable-ref-test",
            "description": "App for testing portable workflow refs",
            "access_level": "authenticated",
            "app_model": "inline_v1",
        },
    )
    assert app_response.status_code == 201, f"Create app failed: {app_response.text}"
    app = app_response.json()
    logger.info(f"Created app: {app['id']}")

    # Create page with launch_workflow_id
    page_response = e2e_client.post(
        f"/api/applications/{app['id']}/pages",
        headers=platform_admin.headers,
        json={
            "page_id": "test-page",
            "title": "Test Page",
            "path": "/test",
            "page_order": 0,
            "launch_workflow_id": test_workflow["id"],
        },
    )
    assert page_response.status_code == 201, f"Create page failed: {page_response.text}"
    page = page_response.json()
    logger.info(f"Created page: {page['page_id']}")

    # Create button component with workflow_id
    component_response = e2e_client.post(
        f"/api/applications/{app['id']}/pages/{page['page_id']}/components",
        headers=platform_admin.headers,
        json={
            "component_id": "btn_workflow",
            "type": "button",
            "props": {
                "label": "Execute Workflow",
                "variant": "default",
                "action_type": "workflow",
                "workflow_id": test_workflow["id"],
            },
            "parent_id": None,
            "component_order": 0,
        },
    )
    assert component_response.status_code == 201, f"Create component failed: {component_response.text}"
    logger.info("Created button component with workflow_id")

    yield {
        "app": app,
        "page": page,
        "workflow": test_workflow,
    }

    # Cleanup: delete app (cascades to pages/components)
    try:
        e2e_client.delete(
            f"/api/applications/{app['id']}",
            headers=platform_admin.headers,
        )
        logger.info(f"Cleaned up app: {app['id']}")
    except Exception as e:
        logger.warning(f"Failed to cleanup app: {e}")


@pytest.fixture
def test_form_with_workflow(
    e2e_client, platform_admin, test_workflow
) -> Generator[dict[str, Any], None, None]:
    """
    Create a form with workflow references.

    Creates form with:
    - workflow_id (for submission processing)
    - launch_workflow_id (for launch behavior)

    Note: We don't test data_provider_id here because it requires a workflow
    with workflow_type="data_provider" which is a more complex setup.

    Yields:
        dict with: form (full form data), workflow (workflow info)
    """
    form_response = e2e_client.post(
        "/api/forms",
        headers=platform_admin.headers,
        json={
            "name": "E2E Portable Ref Test Form",
            "description": "Form for testing portable workflow refs",
            "workflow_id": test_workflow["id"],
            "launch_workflow_id": test_workflow["id"],
            "form_schema": {
                "fields": [
                    {
                        "name": "text_input",
                        "label": "Text Input",
                        "type": "text",
                        "required": True,
                    },
                ]
            },
        },
    )
    assert form_response.status_code == 201, f"Create form failed: {form_response.text}"
    form = form_response.json()
    logger.info(f"Created form: {form['id']}")

    yield {
        "form": form,
        "workflow": test_workflow,
    }

    # Cleanup
    try:
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )
        logger.info(f"Cleaned up form: {form['id']}")
    except Exception as e:
        logger.warning(f"Failed to cleanup form: {e}")


@pytest.fixture
def test_tool_workflow(e2e_client, platform_admin) -> Generator[dict[str, Any], None, None]:
    """
    Create a tool-type workflow for agent testing.

    Tools require workflow_type="tool" to be used as agent tools.
    Uses unique timestamp to avoid conflicts between tests.
    """
    # Use timestamp to ensure unique name/path per test
    unique_id = int(time.time() * 1000) % 1000000
    workflow_name = f"e2e_tool_{unique_id}"
    file_path = f"workflows/e2e_tool_{unique_id}.py"

    workflow_content = f'''"""Test tool workflow for portable ref E2E testing."""
from bifrost import workflow

@workflow(
    name="{workflow_name}",
    description="E2E portable ref test tool",
    is_tool=True
)
def {workflow_name}(query: str) -> str:
    """Search for information."""
    return f"result: {{query}}"
'''

    # Create tool workflow file and register
    result = write_and_register(
        e2e_client, platform_admin.headers,
        file_path, workflow_content, workflow_name,
    )
    logger.info(f"Created and registered tool workflow: {file_path}")

    workflow_info = {
        "id": result["id"],
        "name": result["name"],
        "file_path": file_path,
        "portable_ref": f"{file_path}::{workflow_name}",
    }
    logger.info(f"Tool workflow registered: {workflow_info}")

    yield workflow_info

    # Cleanup: delete workflow file
    try:
        e2e_client.delete(
            f"/api/files/editor?path={file_path}",
            headers=platform_admin.headers,
        )
        logger.info(f"Cleaned up tool workflow file: {file_path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup tool workflow file: {e}")


@pytest.fixture
def test_agent_with_tools(
    e2e_client, platform_admin, test_tool_workflow
) -> Generator[dict[str, Any], None, None]:
    """
    Create an agent with tool references.

    Creates agent with tool_ids referencing a tool-type workflow.

    Yields:
        dict with: agent (full agent data), workflow (workflow info)
    """
    agent_response = e2e_client.post(
        "/api/agents",
        headers=platform_admin.headers,
        json={
            "name": "E2E Portable Ref Test Agent",
            "description": "Agent for testing portable workflow refs",
            "system_prompt": "You are a test agent for portable ref testing.",
            "channels": ["chat"],
            "tool_ids": [test_tool_workflow["id"]],
        },
    )
    assert agent_response.status_code == 201, f"Create agent failed: {agent_response.text}"
    agent = agent_response.json()
    logger.info(f"Created agent: {agent['id']}")

    yield {
        "agent": agent,
        "workflow": test_tool_workflow,
    }

    # Cleanup
    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}",
            headers=platform_admin.headers,
        )
        logger.info(f"Cleaned up agent: {agent['id']}")
    except Exception as e:
        logger.warning(f"Failed to cleanup agent: {e}")


def create_app_json_with_portable_refs(
    app_name: str,
    app_slug: str,
    app_id: str,
    workflow_portable_ref: str,
) -> str:
    """
    Generate .app.json content with portable workflow refs.

    This creates a valid app JSON that uses portable refs instead of UUIDs,
    simulating what would be pushed to GitHub.

    Args:
        app_name: Display name for the application.
        app_slug: URL-friendly slug for the application.
        app_id: Unique identifier for the application (used for sync matching).
        workflow_portable_ref: Portable reference to a workflow (e.g., "path/to/file.py::workflow_name").
    """
    import json as _json

    app_data = {
        "id": app_id,
        "name": app_name,
        "slug": app_slug,
        "description": "App with portable refs for pull testing",
        "pages": [
            {
                "id": "test-page",
                "title": "Test Page",
                "path": "/test",
                "layout": {
                    "id": "layout_1",
                    "type": "column",
                    "children": [
                        {
                            "id": "btn_1",
                            "type": "button",
                            "props": {
                                "label": "Execute",
                                "action_type": "workflow",
                                "workflow_id": workflow_portable_ref,
                            },
                        },
                    ],
                },
                "data_sources": [],
                "launch_workflow_id": workflow_portable_ref,
            }
        ],
        "_export": {
            "workflow_refs": [
                "pages.*.layout..*.props.workflow_id",
                "pages.*.launch_workflow_id",
            ],
            "version": "1.0",
        },
    }
    return _json.dumps(app_data, indent=2)


def create_form_yaml_with_portable_refs(
    form_name: str,
    form_id: str,
    workflow_portable_ref: str,
) -> str:
    """
    Generate .form.yaml content with portable workflow refs.

    Args:
        form_name: Display name for the form.
        form_id: Unique identifier for the form (used for sync matching).
        workflow_portable_ref: Portable reference to a workflow (e.g., "path/to/file.py::workflow_name").
    """
    import yaml

    form_data = {
        "id": form_id,
        "name": form_name,
        "description": "Form with portable refs for pull testing",
        "workflow_id": workflow_portable_ref,
        "launch_workflow_id": workflow_portable_ref,
        "form_schema": {
            "fields": [
                {
                    "name": "text_input",
                    "label": "Text",
                    "type": "text",
                },
                {
                    "name": "select_input",
                    "label": "Select",
                    "type": "select",
                    "data_provider_id": workflow_portable_ref,
                },
            ]
        },
        "_export": {
            "workflow_refs": [
                "workflow_id",
                "launch_workflow_id",
                "form_schema.fields.1.data_provider_id",
            ],
            "version": "1.0",
        },
    }
    return yaml.dump(form_data, default_flow_style=False)


def create_agent_yaml_with_portable_refs(
    agent_name: str,
    agent_id: str,
    workflow_portable_ref: str,
) -> str:
    """
    Generate .agent.yaml content with portable workflow refs.

    Args:
        agent_name: Display name for the agent.
        agent_id: Unique identifier for the agent (used for sync matching).
        workflow_portable_ref: Portable reference to a workflow (e.g., "path/to/file.py::workflow_name").
    """
    import yaml

    agent_data = {
        "id": agent_id,
        "name": agent_name,
        "description": "Agent with portable refs for pull testing",
        "system_prompt": "You are a test agent.",
        "channels": ["chat"],
        "tool_ids": [workflow_portable_ref],
        "_export": {
            "workflow_refs": ["tool_ids.0"],
            "version": "1.0",
        },
    }
    return yaml.dump(agent_data, default_flow_style=False)
