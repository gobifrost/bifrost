"""Shared business service for SDK-consumed form list/get operations.

Single implementation for both entry points:

- the HTTP handlers (``api/src/routers/forms.py::list_forms`` /
  ``get_form``) serving SDK/CLI callers (``api/bifrost/forms.py``), and
- the same handlers reached by workflow children over the worker-local
  engine socket.

Both paths can share scope resolution input (an explicit trusted principal),
the original-caller authorization decision, the 404-before-403 error precedence,
the embed binding gate, logo enrichment, and dependency counts — so HTTP
and worker-local results are identical by construction.

Only the SDK-consumed list/get operations live here. Form
create/update/delete, publication, runtime, logo mutation, and submission
endpoints keep their router-level logic; the logo/access helpers they
share moved here as the single source of truth.

Parent-side only: imports SQLAlchemy repositories. A workflow child never
imports this module (it stays DB-free and reaches it over the engine
socket).
"""

from __future__ import annotations

import base64
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import defer, selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.core.principal import UserPrincipal
from src.models import Form as FormORM
from src.models import FormPublic
from src.models import FormRole as FormRoleORM
from src.models.contracts.forms import FormField, FormSchema
from shared.logo_processing import is_logo_thumbnail_version
from shared.scope_resolver import UNSET, RequestedScope, shared_read_organization
from shared.resource_access import ResourceAccessResolver, ResourceKind

logger = logging.getLogger(__name__)


class SdkFormError(Exception):
    """SDK form list/get failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Missing entities are 404, denied
    access is 403, malformed scope is 422 — matching the historical
    handler responses exactly.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def logo_data_url(data: bytes | None, content_type: str | None) -> str | None:
    """Encode a binary logo as a data URL, or None if no logo is set."""
    if not data:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type or 'application/octet-stream'};base64,{encoded}"


def form_logo_url(form: FormORM) -> str | None:
    """Return a logo URL without hiding legacy images during thumbnail backfill."""
    if is_logo_thumbnail_version(form.logo_thumbnail_version):
        return f"/api/forms/{form.id}/logo?v={form.logo_thumbnail_version}"
    if form.logo_content_type:
        return f"/api/forms/{form.id}/logo"
    return None


def attach_form_logo_fields(
    form_public: FormPublic, form: FormORM, *, include_inline_logo: bool = False
) -> FormPublic:
    """Enrich a ``FormPublic`` with logo fields from its ORM row."""
    form_public.logo = (
        logo_data_url(
            form.logo_thumbnail_data or form.logo_data,
            form.logo_thumbnail_content_type or form.logo_content_type,
        )
        if include_inline_logo
        else None
    )
    form_public.logo_url = form_logo_url(form)
    form_public.logo_version = (
        form.logo_thumbnail_version
        if is_logo_thumbnail_version(form.logo_thumbnail_version)
        else None
    )
    return form_public


def embed_can_access_form(principal: UserPrincipal, form: FormORM) -> bool:
    """Whether an EMBED principal is bound to THIS form (EXT-1 NEW-I).

    An embed token is HMAC-pre-authorized for exactly ONE resource.
    Binding rule (the token must be bound to the form being touched):
    - form-embed token (``form_id`` claim set): must match the form
      exactly — ``principal.form_id == str(form.id)``.
    - app-embed tokens (``app_id`` set) are never form-bound: an app embed
      session has no reason to touch a form, so it is always denied here.
    - a token with neither claim is never form-bound.
    """
    if principal.form_id is not None:
        return principal.form_id == str(form.id)
    return False


async def check_form_access(
    session: AsyncSession,
    form: FormORM,
    caller,
    *,
    selected_organization_id: RequestedScope = UNSET,
) -> bool:
    """Use the common resource resolver for ordinary Form visibility."""
    return await ResourceAccessResolver(session, caller).can_read(
        ResourceKind.FORM,
        form,
        selected_organization_id=selected_organization_id,
    )


async def load_form_role_ids(session: AsyncSession, form_id: UUID) -> list[UUID]:
    """Return the role IDs currently assigned to a form."""
    result = await session.execute(
        select(FormRoleORM.role_id).where(FormRoleORM.form_id == form_id)
    )
    return list(result.scalars().all())


def _apply_dependency_counts(forms: list[FormPublic]) -> None:
    """Fill ``dependency_count`` on list results (workflow + launch + providers)."""
    for form_public in forms:
        count = 0
        if form_public.workflow_id and len(form_public.workflow_id) == 36:
            count += 1
        if form_public.launch_workflow_id and len(form_public.launch_workflow_id) == 36:
            count += 1
        if form_public.form_schema:
            schema = form_public.form_schema
            fields = (
                schema.fields
                if isinstance(schema, FormSchema)
                else (schema or {}).get("fields", [])
            )
            for field in fields:
                dp_id = (
                    field.data_provider_id
                    if isinstance(field, FormField)
                    else (field or {}).get("data_provider_id")
                )
                if dp_id:
                    count += 1
        form_public.dependency_count = count


async def list_sdk_forms(
    session: AsyncSession,
    principal: UserPrincipal,
    scope: str | None = None,
) -> list[FormPublic]:
    """List all forms visible to the principal.

    The omitted scope resolves to the verified caller's default organization
    and includes the Global fallback (a Platform Admin retains its established
    all-organizations listing). ``global`` is exact; an explicit organization
    retains the Global fallback. Every row is still checked at its applicable
    named Form permission target and ordinary form ACL.

    Args:
        session: Database session.
        principal: The auth-verified trusted principal.
        scope: ``None`` selects the caller's default org plus its Global
            fallback (or every org for a Platform Admin); ``'global'`` selects
            Global only; an org UUID selects that organization plus Global.

    Raises:
        SdkFormError: 422 for a malformed scope value.
    """
    from shared.sdk_config import ScopeResolutionError, resolve_sdk_scope
    from src.services.authorization.enforce import allows_permission, load_caller, user_reach

    caller = await load_caller(session, principal)
    try:
        selected_org = await resolve_sdk_scope(scope, caller=caller)
    except ScopeResolutionError as e:
        raise SdkFormError(e.status_code, e.detail) from None
    candidates = list(
        (
            await session.execute(
                select(FormORM)
                .options(
                    selectinload(FormORM.fields),
                    defer(FormORM.logo_data),
                    defer(FormORM.logo_thumbnail_data),
                )
                .order_by(FormORM.name)
            )
        ).scalars().all()
    )
    forms = []
    access = ResourceAccessResolver(session, caller)
    scope_is_unset = scope is None or scope == ""
    selected_permission_scope = UNSET if scope_is_unset else selected_org
    for form in candidates:
        if scope == "global":
            if form.organization_id is not None:
                continue
        elif not scope_is_unset:
            if form.organization_id not in (selected_org, None):
                continue
        elif not user_reach(caller).covers(form.organization_id):
            continue
        permission_org = shared_read_organization(
            caller,
            form.organization_id,
            selected_organization_id=selected_permission_scope,
        )
        if not form.is_active and not allows_permission(
            caller, "forms.read", permission_org
        ):
            continue
        if await access.can_read(
            ResourceKind.FORM,
            form,
            selected_organization_id=selected_permission_scope,
        ):
            forms.append(form)

    result = [attach_form_logo_fields(FormPublic.model_validate(f), f) for f in forms]
    _apply_dependency_counts(result)
    return result


async def get_sdk_form(
    session: AsyncSession,
    principal: UserPrincipal,
    form_id: UUID,
) -> FormPublic:
    """Get a specific form by ID, enforcing the historical access rules.

    Error precedence (unchanged from the HTTP handler):
    1. Missing form → 404 ``"Form not found"``.
    2. Embed principals bound to another resource → 404 (never reveal a
       cross-tenant form's existence).
    3. Inactive forms → 404 unless the caller holds ``forms.read`` at the
       form's actual organization.
    4. Org/role/access_level gate failure → 403 ``"Access denied to form"``.

    Args:
        session: Database session.
        principal: The auth-verified trusted principal.
        form_id: Form UUID.

    Raises:
        SdkFormError: 404 when the form is missing, inactive, or outside
            the principal's embed binding; 403 when access is denied.
    """
    form = (
        await session.execute(
            select(FormORM)
            .options(selectinload(FormORM.fields))
            .where(FormORM.id == form_id)
        )
    ).scalar_one_or_none()

    if not form:
        logger.warning(f"Form {log_safe(form_id)} not found in database")
        raise SdkFormError(404, "Form not found")

    if principal.embed:
        if embed_can_access_form(principal, form):
            return await _to_public(session, form)
        raise SdkFormError(404, "Form not found")

    from src.services.authorization.enforce import allows_permission, load_caller

    caller = await load_caller(session, principal)
    permission_org = shared_read_organization(caller, form.organization_id)
    if not form.is_active and not allows_permission(caller, "forms.read", permission_org):
        raise SdkFormError(404, "Form not found")

    if not await check_form_access(session, form, caller):
        raise SdkFormError(403, "Access denied to form")

    return await _to_public(session, form)


async def _to_public(session: AsyncSession, form: FormORM) -> FormPublic:
    form.role_ids = await load_form_role_ids(session, form.id)  # type: ignore[attr-defined]
    return attach_form_logo_fields(
        FormPublic.model_validate(form),
        form,
        include_inline_logo=True,
    )
