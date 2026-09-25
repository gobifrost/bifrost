"""Focused unit tests for the SDK forms/context shared services.

Covers ``shared.sdk_forms`` (list/get with unchanged scope resolution,
error precedence, embed binding, logo enrichment, dependency counts) and
``shared.sdk_context`` (org override C2 gate, missing/inactive orgs).
DB-backed via the ``db_session`` fixture; HTTP handlers delegate to these
same functions, so handler-level e2e (``test_cli.py``, ``test_forms.py``)
covers the transport mapping.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared.sdk_context import SdkContextError, get_sdk_context
from shared.sdk_forms import SdkFormError, get_sdk_form, list_sdk_forms
from src.core.principal import UserPrincipal
from src.models.enums import FormAccessLevel


def _principal(org_id=None, **kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk-forms@test.local"),
        organization_id=org_id,
        name=kwargs.get("name", "SDK Forms"),
        is_superuser=kwargs.get("is_superuser", False),
        is_external=kwargs.get("is_external", False),
        embed=kwargs.get("embed", False),
        app_id=kwargs.get("app_id"),
        form_id=kwargs.get("form_id"),
    )


async def _seed_org(db_session, *, is_provider=False, is_active=True):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-forms-org-{uuid4().hex[:8]}",
        is_active=is_active,
        is_provider=is_provider,
        created_by="sdk-forms-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_form(db_session, name, *, org_id=None, **kwargs):
    from src.models.orm.forms import Form as FormModel
    from src.models.orm.forms import FormField as FormFieldModel

    row = FormModel(
        name=name,
        access_level=kwargs.get("access_level", FormAccessLevel.AUTHENTICATED),
        organization_id=org_id,
        is_active=kwargs.get("is_active", True),
        workflow_id=kwargs.get("workflow_id"),
        launch_workflow_id=kwargs.get("launch_workflow_id"),
        logo_content_type=kwargs.get("logo_content_type"),
        logo_thumbnail_version=kwargs.get("logo_thumbnail_version"),
        logo_thumbnail_data=kwargs.get("logo_thumbnail_data"),
        logo_thumbnail_content_type=kwargs.get("logo_thumbnail_content_type"),
        created_by="sdk-forms-test",
    )
    db_session.add(row)
    await db_session.flush()
    if kwargs.get("with_provider_field"):
        from src.models.orm.workflows import Workflow as WorkflowModel

        provider = WorkflowModel(
            name=f"sdk-forms-provider-{uuid4().hex[:8]}",
            function_name="provider_fn",
            path="workflows/provider.py",
            type="data_provider",
            is_active=True,
        )
        db_session.add(provider)
        await db_session.flush()
        db_session.add(
            FormFieldModel(
                form_id=row.id,
                name="tenant",
                label="Tenant",
                type="text",
                required=False,
                position=0,
                data_provider_id=provider.id,
            )
        )
        await db_session.flush()
    return row


class TestListSdkForms:
    async def test_superuser_sees_all_including_inactive(self, db_session) -> None:
        org = await _seed_org(db_session)
        await _seed_form(db_session, "active-global")
        await _seed_form(db_session, "inactive-org", org_id=org.id, is_active=False)

        result = await list_sdk_forms(
            db_session, _principal(is_superuser=True), scope=None
        )

        names = {f.name for f in result}
        assert {"active-global", "inactive-org"} <= names

    async def test_superuser_global_scope_hides_org_forms(self, db_session) -> None:
        org = await _seed_org(db_session)
        await _seed_form(db_session, "global-one")
        await _seed_form(db_session, "org-one", org_id=org.id)

        result = await list_sdk_forms(
            db_session, _principal(is_superuser=True), scope="global"
        )

        assert [f.name for f in result] == ["global-one"]

    async def test_org_user_sees_own_plus_global_active_only(
        self, db_session
    ) -> None:
        org1 = await _seed_org(db_session)
        org2 = await _seed_org(db_session)
        await _seed_form(db_session, "global-one")
        await _seed_form(db_session, "own-one", org_id=org1.id)
        await _seed_form(db_session, "own-inactive", org_id=org1.id, is_active=False)
        await _seed_form(db_session, "other-one", org_id=org2.id)

        result = await list_sdk_forms(db_session, _principal(org1.id))

        assert sorted(f.name for f in result) == ["global-one", "own-one"]

    async def test_invalid_scope_is_422(self, db_session) -> None:
        with pytest.raises(SdkFormError) as exc_info:
            await list_sdk_forms(
                db_session, _principal(is_superuser=True), scope="not-a-uuid"
            )

        assert exc_info.value.status_code == 422

    async def test_dependency_counts(self, db_session) -> None:
        await _seed_form(
            db_session,
            "counted",
            workflow_id=str(uuid4()),
            launch_workflow_id=str(uuid4()),
            with_provider_field=True,
        )

        (form,) = await list_sdk_forms(db_session, _principal(is_superuser=True))

        assert form.dependency_count == 3

    async def test_list_logo_enrichment_without_inline_bytes(
        self, db_session
    ) -> None:
        version = "d" * 64
        form = await _seed_form(
            db_session,
            "logo-form",
            logo_content_type="image/png",
            logo_thumbnail_version=version,
            logo_thumbnail_data=b"thumb",
            logo_thumbnail_content_type="image/webp",
        )

        (listed,) = await list_sdk_forms(db_session, _principal(is_superuser=True))

        assert listed.logo is None
        assert listed.logo_url == f"/api/forms/{form.id}/logo?v={version}"
        assert listed.logo_version == version


class TestGetSdkForm:
    async def test_missing_form_is_404(self, db_session) -> None:
        with pytest.raises(SdkFormError) as exc_info:
            await get_sdk_form(db_session, _principal(is_superuser=True), uuid4())

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Form not found"

    async def test_superuser_gets_inactive_form_with_inline_logo(
        self, db_session
    ) -> None:
        version = "e" * 64
        form = await _seed_form(
            db_session,
            "inactive-logo",
            is_active=False,
            logo_content_type="image/png",
            logo_thumbnail_version=version,
            logo_thumbnail_data=b"thumb",
            logo_thumbnail_content_type="image/webp",
        )

        result = await get_sdk_form(db_session, _principal(is_superuser=True), form.id)

        assert result.name == "inactive-logo"
        assert result.logo is not None
        assert result.logo_url == f"/api/forms/{form.id}/logo?v={version}"

    async def test_org_user_inactive_form_is_404(self, db_session) -> None:
        org = await _seed_org(db_session)
        form = await _seed_form(
            db_session, "inactive-own", org_id=org.id, is_active=False
        )

        with pytest.raises(SdkFormError) as exc_info:
            await get_sdk_form(db_session, _principal(org.id), form.id)

        assert exc_info.value.status_code == 404

    async def test_org_user_other_org_form_is_403(self, db_session) -> None:
        org1 = await _seed_org(db_session)
        org2 = await _seed_org(db_session)
        form = await _seed_form(db_session, "other-org", org_id=org2.id)

        with pytest.raises(SdkFormError) as exc_info:
            await get_sdk_form(db_session, _principal(org1.id), form.id)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Access denied to form"

    async def test_external_user_denied_authenticated_form(self, db_session) -> None:
        org = await _seed_org(db_session)
        form = await _seed_form(db_session, "auth-form", org_id=org.id)

        with pytest.raises(SdkFormError) as exc_info:
            await get_sdk_form(
                db_session, _principal(org.id, is_external=True), form.id
            )

        assert exc_info.value.status_code == 403

    async def test_form_embed_token_bound_to_form(self, db_session) -> None:
        org = await _seed_org(db_session)
        form = await _seed_form(db_session, "embed-form", org_id=org.id)

        bound = _principal(org.id, embed=True, form_id=str(form.id))
        assert (await get_sdk_form(db_session, bound, form.id)).name == "embed-form"

        unbound = _principal(org.id, embed=True, form_id=str(uuid4()))
        with pytest.raises(SdkFormError) as exc_info:
            await get_sdk_form(db_session, unbound, form.id)
        assert exc_info.value.status_code == 404

    async def test_app_embed_token_scoped_to_own_org(self, db_session) -> None:
        org1 = await _seed_org(db_session)
        org2 = await _seed_org(db_session)
        own = await _seed_form(db_session, "own-form", org_id=org1.id)
        other = await _seed_form(db_session, "other-form", org_id=org2.id)
        token = _principal(org1.id, embed=True, app_id="app-1")

        assert (await get_sdk_form(db_session, token, own.id)).name == "own-form"
        with pytest.raises(SdkFormError) as exc_info:
            await get_sdk_form(db_session, token, other.id)
        assert exc_info.value.status_code == 404


class TestGetSdkContext:
    async def test_superuser_can_target_other_org(self, db_session) -> None:
        org = await _seed_org(db_session)

        data = await get_sdk_context(
            db_session, _principal(is_superuser=True), org_id=org.id
        )

        assert data["organization"]["id"] == str(org.id)
        assert data["organization"]["name"] == org.name
        assert data["default_parameters"] == {}
        assert data["track_executions"] is True

    async def test_non_bypass_caller_cannot_target_other_org(
        self, db_session
    ) -> None:
        org1 = await _seed_org(db_session)
        org2 = await _seed_org(db_session)

        with pytest.raises(SdkContextError) as exc_info:
            await get_sdk_context(db_session, _principal(org1.id), org_id=org2.id)

        assert exc_info.value.status_code == 403

    async def test_provider_org_member_can_target_other_org(
        self, db_session
    ) -> None:
        provider = await _seed_org(db_session, is_provider=True)
        other = await _seed_org(db_session)

        data = await get_sdk_context(
            db_session, _principal(provider.id), org_id=other.id
        )

        assert data["organization"]["id"] == str(other.id)

    async def test_defaults_to_caller_org(self, db_session) -> None:
        org = await _seed_org(db_session)

        data = await get_sdk_context(db_session, _principal(org.id))

        assert data["organization"]["id"] == str(org.id)
        assert data["user"]["is_superuser"] is False

    async def test_missing_org_is_404(self, db_session) -> None:
        with pytest.raises(SdkContextError) as exc_info:
            await get_sdk_context(
                db_session, _principal(is_superuser=True), org_id=uuid4()
            )

        assert exc_info.value.status_code == 404

    async def test_inactive_org_is_404(self, db_session) -> None:
        org = await _seed_org(db_session, is_active=False)

        with pytest.raises(SdkContextError) as exc_info:
            await get_sdk_context(
                db_session, _principal(is_superuser=True), org_id=org.id
            )

        assert exc_info.value.status_code == 404

    async def test_caller_without_org_gets_null_organization(
        self, db_session
    ) -> None:
        data = await get_sdk_context(db_session, _principal(is_superuser=True))

        assert data["organization"] is None
