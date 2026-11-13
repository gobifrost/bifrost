"""Form MCP tools forward the complete optional layout to the REST contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.mcp_server.tools import forms


@pytest.fixture
def rest(monkeypatch):
    async def assemble(_context, fields, *, is_update, scope):
        return {key: value for key, value in fields.items() if value is not None}

    monkeypatch.setattr(forms, "_assemble_form_body", assemble)
    monkeypatch.setattr(forms, "_resolve_ref", AsyncMock(return_value="form-id"))
    call = AsyncMock(return_value=(200, {"id": "form-id", "name": "Contact"}))
    monkeypatch.setattr(forms, "call_rest", call)
    return call


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_form_layout_is_forwarded_with_fields(rest, operation):
    fields = [{"name": "email", "type": "email", "label": "Email"}]
    layout = {"sections": [{"rows": [{"field": "email"}]}]}
    context = SimpleNamespace()
    if operation == "create":
        await forms.bifrost_form_create(context, "Contact", "workflow-id", fields, layout=layout)
        method, path = "POST", "/api/forms"
    else:
        await forms.bifrost_form_update(context, "form-id", fields=fields, layout=layout)
        method, path = "PATCH", "/api/forms/form-id"
    args, kwargs = rest.call_args
    assert args == (context, method, path)
    assert kwargs["json_body"]["form_schema"] == {"fields": fields, "layout": layout}


async def test_metadata_update_leaves_schema_untouched(rest):
    await forms.bifrost_form_update(SimpleNamespace(), "form-id", name="Renamed")
    assert rest.call_args.kwargs["json_body"] == {"name": "Renamed"}


async def test_layout_update_requires_fields(rest):
    result = await forms.bifrost_form_update(SimpleNamespace(), "form-id", layout={"sections": []})
    rest.assert_not_awaited()
    assert "fields is required" in str(result)


async def test_replacing_fields_without_layout_returns_to_vertical_form(rest):
    fields = [{"name": "email", "type": "email", "label": "Email"}]
    await forms.bifrost_form_update(SimpleNamespace(), "form-id", fields=fields)
    assert rest.call_args.kwargs["json_body"]["form_schema"] == {"fields": fields, "layout": None}
