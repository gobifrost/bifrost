"""The MCP registration wrapper preserves explicit scope intent on the wire."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["omitted", "null", "global", "org"])
async def test_mcp_registration_forwards_scope_intent(target):
    organization_id = str(uuid4())
    arguments = {"path": "workflows/example.py", "function_name": "example"}
    if target == "null":
        arguments["organization_id"] = None
    elif target == "global":
        arguments["is_global"] = True
    elif target == "org":
        arguments["organization_id"] = "Customer"
    context = MagicMock()
    resolver = MagicMock(resolve=AsyncMock(return_value=organization_id))
    rest = AsyncMock(return_value=(201, {"id": str(uuid4())}))
    with (
        patch.object(workflow, "rest_client", return_value=AsyncMock()),
        patch("bifrost.refs.RefResolver", return_value=resolver),
        patch.object(workflow, "call_rest", rest),
    ):
        await workflow.bifrost_workflow_register(context, **arguments)
    body = {"path": "workflows/example.py", "function_name": "example"}
    if target in ("global", "org"):
        body["organization_id"] = organization_id if target == "org" else None
    rest.assert_awaited_once_with(context, "POST", "/api/workflows/register", json_body=body)


@pytest.mark.asyncio
async def test_mcp_registration_refuses_conflicting_scope_targets():
    rest = AsyncMock()
    with (
        patch.object(workflow, "rest_client", return_value=AsyncMock()),
        patch.object(workflow, "call_rest", rest),
    ):
        result = await workflow.bifrost_workflow_register(
            MagicMock(), "workflows/example.py", "example",
            organization_id=str(uuid4()), is_global=True,
        )
    assert result.is_error
    rest.assert_not_awaited()
