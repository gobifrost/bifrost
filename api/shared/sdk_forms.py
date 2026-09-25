"""Shared business service for SDK-consumed form list/get operations.

Single implementation for both entry points:

- the HTTP handlers (``api/src/routers/forms.py::list_forms`` /
  ``get_form``) serving SDK/CLI callers (``api/bifrost/forms.py``), and
- the engine-local dispatcher once form calls are wired through the
  parent-side local transport.

Both paths can share scope resolution input (an explicit trusted principal),
the superuser/org-user query split, the 404-before-403 error precedence,
the embed binding gate, logo enrichment, and dependency counts — so HTTP
and local results are identical by construction.

Only the SDK-consumed list/get operations live here. Form
create/update/delete, publication, runtime, logo mutation, and submission
endpoints keep their router-level logic; the logo/access helpers they
share moved here as the single source of truth.

Parent-side only: imports SQLAlchemy repositories. A workflow child never
imports this module (it stays DB-free behind the dedicated local
channel).
"""

from __future__ import annotations

import base64
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.core.org_filter import resolve_org_filter
from src.core.principal import UserPrincipal
from src.models import Form as FormORM
from src.models import FormPublic
from src.models import FormRole as FormRoleORM
from src.models.contracts.forms import FormField, FormSchema
from src.repositories.forms import FormRepository
from shared.logo_processing import is_logo_thumbnail_version

logger = logging.getLogger(__name__)


class SdkFormError(Exception):
    """SDK form list/get failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and the local dispatcher (``ok: false`` frames) can map the same
    failure to their own transport. Missing entities are 404, denied
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
    Binding rules (the token must be bound to the form being touched):
    - form-embed token (``form_id`` claim set): must match the form
      exactly — ``principal.form_id == str(form.id)``.
    - app-embed token (``app_id`` set, no ``form_id``): the form must live in
      the embed's OWN org (the form's org equals the token's org — a concrete
      org). A global form (org-id None) is never embed-reachable, and a
      cross-org form is rejected.
    - a token with neither claim is never form-bound.
    """
    if principal.form_id is not None:
        return principal.form_id == str(form.id)
    if principal.app_id is not None:
        form_org = getattr(form, "organization_id", None)
        return form_org is not None and form_org == principal.organization_id
    return False


async def check_form_access(
    session: AsyncSession,
    form: FormORM,
    user_id: UUID,
    user_org_id: UUID | None,
    is_superuser: bool,
    is_external: bool = False,
) -> bool:
    """Check if a user has access to a form.

    Delegates to ``FormRepository.get(id=...)``, which is the single
    org+role+access_level gate shared with the UI listing path and every
    other org-scoped entity (workflows, agents, tables, etc.). Returning
    None from the repo means "form doesn't exist OR caller doesn't have
    access" — for an existing form (the caller already loaded it via raw
    lookup), None definitively means "access denied".
    """
    repo = FormRepository(
        session,
        org_id=user_org_id,
        user_id=user_id,
        is_superuser=is_superuser,
        is_external=is_external,
    )
    accessible = await repo.get(id=form.id)
    return accessible is not None


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

    - Platform admins see all forms (or filter by scope if provided);
      role checks are bypassed and inactive forms are included.
    - Org users see their org's forms + global forms (org_id IS NULL),
      active only, further filtered by access_level (authenticated,
      role_based).

    Args:
        session: Database session.
        principal: The auth-verified trusted principal.
        scope: Filter scope — omit for all (superusers), ``'global'`` for
            global only, or an org UUID for a specific org (org users
            always resolve to their own org + global; scope ignored).

    Raises:
        SdkFormError: 422 for a malformed scope value.
    """
    try:
        filter_type, filter_org = resolve_org_filter(principal, scope)
    except ValueError as e:
        raise SdkFormError(422, str(e)) from None

    repo = FormRepository(
        session=session,
        org_id=filter_org,
        user_id=principal.user_id if not principal.is_superuser else None,
        is_superuser=principal.is_superuser,
        is_external=principal.is_external,
    )

    if principal.is_superuser:
        forms = await repo.list_all_in_scope(filter_type=filter_type, active_only=False)
    else:
        forms = await repo.list_forms(active_only=True)

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
    2. Platform admins see all forms.
    3. Embed principals bound to another resource → 404 (never reveal a
       cross-tenant form's existence).
    4. Inactive form for non-admins → 404.
    5. Org/role/access_level gate failure → 403 ``"Access denied to form"``.

    Args:
        session: Database session.
        principal: The auth-verified trusted principal.
        form_id: Form UUID.

    Raises:
        SdkFormError: 404 when the form is missing, inactive, or outside
            the principal's embed binding; 403 when access is denied.
    """
    repo = FormRepository(
        session=session,
        org_id=None,
        user_id=principal.user_id if not principal.is_superuser else None,
        is_superuser=principal.is_superuser,
        is_external=principal.is_external,
    )
    form = await repo.get_form(form_id)

    if not form:
        logger.warning(f"Form {log_safe(form_id)} not found in database")
        raise SdkFormError(404, "Form not found")

    if principal.is_superuser:
        return await _to_public(session, form)

    if principal.embed:
        if embed_can_access_form(principal, form):
            return await _to_public(session, form)
        raise SdkFormError(404, "Form not found")

    if not form.is_active:
        raise SdkFormError(404, "Form not found")

    if not await check_form_access(
        session,
        form,
        principal.user_id,
        principal.organization_id,
        principal.is_superuser,
        is_external=principal.is_external,
    ):
        raise SdkFormError(403, "Access denied to form")

    return await _to_public(session, form)


async def _to_public(session: AsyncSession, form: FormORM) -> FormPublic:
    form.role_ids = await load_form_role_ids(session, form.id)  # type: ignore[attr-defined]
    return attach_form_logo_fields(
        FormPublic.model_validate(form),
        form,
        include_inline_logo=True,
    )
