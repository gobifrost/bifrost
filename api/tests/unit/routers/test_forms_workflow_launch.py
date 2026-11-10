"""Authorization boundaries for form-triggered workflow launches."""

from src.core.storage.contracts import SignedRequest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models import FileUploadRequest
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.routers import forms


def _caller(*, home, permissions=()) -> Caller:
    principal = UserPrincipal(
        user_id=uuid4(), email="forms-launch@contoso.example", organization_id=home
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=home,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(permissions),
            role_grants=(),
        ),
    )


def _context(*, embed=False, org_id=None):
    return SimpleNamespace(
        user=SimpleNamespace(embed=embed),
        org_id=org_id,
    )


@pytest.mark.asyncio
async def test_authenticated_global_form_requires_execute_at_original_home(monkeypatch):
    home = uuid4()
    caller = _caller(home=home)
    monkeypatch.setattr(forms, "load_caller", AsyncMock(return_value=caller))

    with pytest.raises(HTTPException) as exc_info:
        await forms._form_launch_authority(
            AsyncMock(), _context(org_id=uuid4()), SimpleNamespace(organization_id=None)
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticated_global_form_uses_original_home_execute_grant(monkeypatch):
    home = uuid4()
    caller = _caller(home=home, permissions={"workflows.execute"})
    monkeypatch.setattr(forms, "load_caller", AsyncMock(return_value=caller))

    execution_org, resolved_caller = await forms._form_launch_authority(
        AsyncMock(), _context(org_id=uuid4()), SimpleNamespace(organization_id=None)
    )

    assert execution_org == home
    assert resolved_caller is caller


@pytest.mark.asyncio
async def test_authenticated_form_cannot_use_home_execute_grant_for_foreign_org(monkeypatch):
    home = uuid4()
    caller = _caller(home=home, permissions={"workflows.execute"})
    monkeypatch.setattr(forms, "load_caller", AsyncMock(return_value=caller))

    with pytest.raises(HTTPException) as exc_info:
        await forms._form_launch_authority(
            AsyncMock(), _context(org_id=home), SimpleNamespace(organization_id=uuid4())
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_bound_embed_form_keeps_its_resource_capability_without_human_grant(monkeypatch):
    load_caller = AsyncMock()
    monkeypatch.setattr(forms, "load_caller", load_caller)
    form_org = uuid4()

    execution_org, caller = await forms._form_launch_authority(
        AsyncMock(), _context(embed=True, org_id=form_org), SimpleNamespace(organization_id=None)
    )

    assert execution_org == form_org
    assert caller is None
    load_caller.assert_not_awaited()


@pytest.mark.asyncio
async def test_scoped_form_rejects_a_different_organization_override(monkeypatch):
    home = uuid4()
    caller = _caller(home=home, permissions={"workflows.execute"})
    monkeypatch.setattr(forms, "load_caller", AsyncMock(return_value=caller))

    with pytest.raises(HTTPException) as exc_info:
        await forms._form_launch_authority(
            AsyncMock(),
            _context(org_id=home),
            SimpleNamespace(organization_id=home),
            requested_organization_id=uuid4(),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_embed_form_rejects_an_organization_override(monkeypatch):
    load_caller = AsyncMock()
    monkeypatch.setattr(forms, "load_caller", load_caller)

    with pytest.raises(HTTPException) as exc_info:
        await forms._form_launch_authority(
            AsyncMock(),
            _context(embed=True, org_id=uuid4()),
            SimpleNamespace(organization_id=None),
            requested_organization_id=uuid4(),
        )

    assert exc_info.value.status_code == 403
    load_caller.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_uses_the_selected_execution_organization(monkeypatch):
    selected_org = uuid4()
    form = SimpleNamespace(id=uuid4(), is_active=True, fields=[])
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: form)
    user = SimpleNamespace(embed=False)
    ctx = SimpleNamespace(user=user, org_id=uuid4())
    launch = AsyncMock(return_value=(selected_org, SimpleNamespace()))
    monkeypatch.setattr(forms, "_form_launch_authority", launch)
    monkeypatch.setattr(forms, "_authorize_form_runtime", AsyncMock())

    from src.services import file_storage
    from shared import file_paths

    storage = SimpleNamespace(
        generate_signed_upload_request=AsyncMock(return_value=SignedRequest("PUT", "https://uploads.example/file", {"x-ms-blob-type": "BlockBlob"}, 600))
    )
    monkeypatch.setattr(file_storage, "FileStorageService", MagicMock(return_value=storage))
    captured: dict[str, str] = {}

    def resolve_s3_key(location: str, scope: str, path: str) -> str:
        captured.update(location=location, scope=scope, path=path)
        return f"uploads/{scope}/{path}"

    monkeypatch.setattr(file_paths, "resolve_s3_key", resolve_s3_key)

    await forms.generate_upload_url(
        form.id,
        SimpleNamespace(),
        FileUploadRequest(
            file_name="report.txt", content_type="text/plain", file_size=12
        ),
        ctx,
        user,
        db,
        org_id=selected_org,
    )

    launch.assert_awaited_once_with(
        db, ctx, form, requested_organization_id=selected_org
    )
    assert storage.generate_signed_upload_request.await_args.kwargs["content_length"] == 12
    assert captured["location"] == "uploads"
    assert captured["scope"] == str(selected_org)


@pytest.mark.asyncio
async def test_upload_preserves_form_visibility_before_checking_scope_override(monkeypatch):
    form = SimpleNamespace(id=uuid4(), is_active=True, fields=[])
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: form)
    user = SimpleNamespace(embed=True)
    ctx = SimpleNamespace(user=user, org_id=uuid4())
    launch = AsyncMock()
    monkeypatch.setattr(forms, "_form_launch_authority", launch)
    monkeypatch.setattr(
        forms, "_authorize_form_runtime",
        AsyncMock(side_effect=HTTPException(status_code=404, detail="Form unavailable")),
    )
    with pytest.raises(HTTPException) as exc:
        await forms.generate_upload_url(
            form.id, SimpleNamespace(),
            FileUploadRequest(file_name="report.txt", content_type="text/plain", file_size=12),
            ctx, user, db, org_id=uuid4(),
        )
    assert exc.value.status_code == 404
    launch.assert_not_awaited()
