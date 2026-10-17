"""Optional form metadata distinguishes explicit clearing from omitted updates."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.contracts.forms import FormUpdate
from src.models.orm.forms import Form
from src.routers import forms


@pytest.mark.parametrize("clear", [False, True])
async def test_optional_form_metadata_patch(db_session, monkeypatch, clear):
    form_id = uuid4()
    original = {
        "description": "Original description",
        "launch_workflow_id": "original-launch",
        "default_launch_params": {"region": "east"},
        "allowed_query_params": ["region"],
    }
    record = Form(id=form_id, name="Patch review", created_by="test", **original)
    db_session.add(record)
    await db_session.flush()
    monkeypatch.setattr(forms, "_validate_form_references", AsyncMock())
    monkeypatch.setattr(forms, "_require_form_permission", AsyncMock())
    monkeypatch.setattr(forms, "sync_form_roles_to_workflows", AsyncMock())
    monkeypatch.setattr(forms, "CACHE_INVALIDATION_AVAILABLE", False)
    request = FormUpdate(**({key: None for key in original} if clear else {"name": "Renamed"}))
    await forms.update_form(
        form_id, request,
        SimpleNamespace(user=SimpleNamespace(email="review@example.invalid", embed=False)),
        None, db_session,
    )
    await db_session.refresh(record)
    for key, value in original.items():
        assert getattr(record, key) == (None if clear else value)
