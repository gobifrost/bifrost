"""
Tables Router

Manage tables and documents for app builder data storage.
Uses OrgScopedRepository for standardized org scoping.

Tables follow the same scoping pattern as configs:
- organization_id = NULL: Global table (platform-wide)
- organization_id = UUID: Organization-scoped table
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.claims.registry import referenced_claim_names
from shared.table_document_writes import (
    BatchDocumentInput,
    TableWriteError,
    batch_delete_table_documents,
    batch_write_table_documents,
    delete_table_document,
    insert_table_document,
    update_table_document,
    upsert_table_document,
)
from shared.table_documents import (
    count_table_documents,
    get_table_document,
    query_table_documents,
)
from shared.table_resolution import (
    assert_explicit_scope_targets_table as _assert_explicit_scope_targets_table,
    assert_solution_write_targets_owned_table as _assert_solution_write_targets_owned_table,
    get_table_or_404,
    resolve_target_org_safe as _resolve_target_org_safe,
)
from src.core.auth import Context, CurrentSuperuser
from src.core.org_filter import resolve_org_filter
from src.models.contracts.policies import (
    PolicyRuleRef,
    PolicyValidationError,
    PolicyValidationResponse,
    TablePolicies,
)
from src.models.contracts.tables import (
    DocumentBatchCreate,
    DocumentBatchCreateResponse,
    DocumentBatchDeleteRequest,
    DocumentBatchDeleteResponse,
    DocumentCountResponse,
    DocumentCreate,
    DocumentListResponse,
    DocumentPublic,
    DocumentQuery,
    DocumentUpdate,
    DocumentUpsert,
    TableCreate,
    TableListResponse,
    TablePublic,
    TableUpdate,
)
from src.models.orm.custom_claims import CustomClaim as CustomClaimORM
from src.models.orm.tables import Table
from src.services.solutions.guard import assert_entity_id_not_solution_managed
from src.repositories.tables import TableRepository
from src.core.pubsub import (
    publish_policy_changed,
)

router = APIRouter(prefix="/api/tables", tags=["Tables"])



def _table_write_http_error(exc: TableWriteError) -> HTTPException:
    """Map a transport-neutral service error to its HTTP equivalent."""
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# =============================================================================
# Helper functions
# =============================================================================


def _validate_policy_claim_refs(
    expr: object,
    known_claim_names: set[str],
) -> None:
    """Reject policy claim references not defined in the table's org."""
    refs = referenced_claim_names(expr)
    missing = refs - known_claim_names
    if missing:
        raise ValueError(
            f"policy references unknown claims: {sorted(missing)}; "
            f"defined in this org: {sorted(known_claim_names)}"
        )


async def _known_claim_names_for_org(
    db: AsyncSession,
    organization_id: UUID | None,
    solution_id: UUID | None = None,
) -> set[str]:
    from sqlalchemy import or_

    stmt = select(CustomClaimORM.name).where(CustomClaimORM.organization_id == organization_id)
    if solution_id is None:
        stmt = stmt.where(CustomClaimORM.solution_id.is_(None))
    else:
        stmt = stmt.where(
            or_(
                CustomClaimORM.solution_id == solution_id,
                CustomClaimORM.solution_id.is_(None),
            )
        )
    rows = (
        await db.execute(stmt)
    ).scalars().all()
    return set(rows)


async def _validate_table_policy_claim_refs(
    db: AsyncSession,
    organization_id: UUID | None,
    policies: TablePolicies | None,
    solution_id: UUID | None = None,
) -> None:
    if policies is None:
        return
    known = await _known_claim_names_for_org(db, organization_id, solution_id)
    for policy in policies.policies:
        if isinstance(policy, PolicyRuleRef):
            continue
        _validate_policy_claim_refs(policy.when, known)


# =============================================================================
# Table Endpoints
# =============================================================================


@router.post(
    "",
    response_model=TablePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a table",
)
async def create_table(
    data: TableCreate,
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Target scope: 'global' or org UUID. Defaults to current org.",
    ),
) -> TablePublic:
    """Create a new table for storing documents (platform admin only)."""
    if ctx.solution_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tables must be declared by the solution manifest",
        )

    # Prefer organization_id from request body; fall back to scope query param (legacy)
    if "organization_id" in (data.model_fields_set or set()):
        target_org_id = data.organization_id
    elif scope is not None:
        target_org_id = _resolve_target_org_safe(ctx, scope)
    else:
        target_org_id = ctx.org_id
    try:
        await _validate_table_policy_claim_refs(ctx.db, target_org_id, data.policies)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    repo = TableRepository(ctx.db, target_org_id, is_superuser=True)
    try:
        table = await repo.create_table(data, created_by=user.email)
        return TablePublic.model_validate(table)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )


@router.get(
    "",
    response_model=TableListResponse,
    summary="List tables",
)
async def list_tables(
    ctx: Context,
    user: CurrentSuperuser,
    scope: str | None = Query(
        default=None,
        description="Filter scope: 'global' for global only, org UUID for specific org.",
    ),
) -> TableListResponse:
    """List all tables in the current scope (platform admin only)."""
    try:
        filter_type, filter_org = resolve_org_filter(ctx.user, scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    repo = TableRepository(ctx.db, filter_org, is_superuser=True)
    tables = await repo.list_tables(filter_type)

    return TableListResponse(
        tables=[TablePublic.model_validate(t) for t in tables],
        total=len(tables),
    )


def _loc_to_path(loc: tuple[Any, ...]) -> str:
    """Convert a Pydantic ``error['loc']`` tuple to the JSONPath-like form
    the AST validator emits (``$.policies[0].when.eq[1]``).

    Integer locs are array indices and attach to the previous segment;
    string locs are dotted-property segments. Returns ``$`` for an empty
    loc (e.g. a top-level type error before any field was reached).
    """
    parts = ["$"]
    for x in loc:
        if isinstance(x, int):
            parts[-1] = parts[-1] + f"[{x}]"
        else:
            parts.append(str(x))
    return ".".join(parts) if len(parts) > 1 else "$"


def _split_value_error_msg(msg: str) -> tuple[str, bool, str]:
    """Split a Pydantic-wrapped Expr ``ValueError`` message back into its
    embedded ``$.<path>: <message>`` parts.

    Pydantic v2 prefixes ``ValueError`` raises from custom validators with
    the literal ``"Value error, "``. The wrapped message itself starts
    with the AST validator's own ``$.<path>: `` prefix (added by
    ``_validate_operand`` so error context survives the call stack).

    Returns ``(inner_path, separator_present, message)``. When the message
    doesn't match the expected shape, returns ``("$", False, msg)`` so the
    caller can fall through and use the loc-derived path verbatim.
    """
    PREFIX = "Value error, "
    body = msg[len(PREFIX):] if msg.startswith(PREFIX) else msg
    if not body.startswith("$"):
        return ("$", False, msg)
    path, sep, rest = body.partition(": ")
    if not sep:
        return ("$", False, msg)
    return (path, True, rest)


@router.post(
    "/policies/validate",
    response_model=PolicyValidationResponse,
    summary="Validate a TablePolicies document without persisting it.",
    description=(
        "Runs the same AST validator the table create/update endpoints use, "
        "returning structured errors. Used by the policy editor for live "
        "feedback. On save, the create/update endpoints validate "
        "authoritatively. Always returns 200 — the validation outcome is in "
        "the body, not the status code."
    ),
)
async def validate_policies(
    ctx: Context,
    user: CurrentSuperuser,
    body: Any = Body(...),
) -> PolicyValidationResponse:
    """Validate a candidate ``TablePolicies`` payload.

    The body is typed as ``dict | list`` (rather than ``TablePolicies``) so
    FastAPI doesn't intercept validation errors as 422 before this handler
    runs — we want to capture the full error list and surface it as
    structured ``{path, message}`` entries in the response body. Anything
    that isn't a JSON object (e.g. a list at the root, or a non-object
    primitive) collapses to a single root-level error.

    The validator (``Expr``) raises ``ValueError`` with messages already
    prefixed by their AST path (``$.policies[0].when.eq[1]: ...``); we
    split that prefix back out into the structured ``path``/``message``
    pair. Pydantic's own ``ValidationError`` (e.g. wrong type for
    ``actions``) goes through the standard ``loc``-tuple → path conversion
    via ``_loc_to_path``.

    Auth: matches the rest of the tables router (``CurrentSuperuser``).
    The validator does not touch any tenant data, but tables are
    superuser-only resources so the endpoint should not be reachable to
    non-admin callers either.
    """
    if not isinstance(body, dict):
        return PolicyValidationResponse(
            ok=False,
            errors=[
                PolicyValidationError(
                    path="$",
                    message="root must be an object {policies: [...]}",
                )
            ],
        )

    try:
        parsed = TablePolicies.model_validate(body)
        # Validate $ref entries resolve to real rules before reporting ok=True.
        from shared.policy_rules import PolicyRuleDomainMismatch, PolicyRuleNotFound, resolve_policy_refs
        from src.repositories.policy_rule import PolicyRuleRepository
        ref_repo = PolicyRuleRepository(ctx.db, org_id=None, is_superuser=True)
        try:
            await resolve_policy_refs(parsed.model_copy(deep=True), repo=ref_repo, action_domain="table")
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as ref_exc:
            return PolicyValidationResponse(
                ok=False,
                errors=[PolicyValidationError(path="$.policies", message=str(ref_exc))],
            )
        return PolicyValidationResponse(ok=True)
    except ValidationError as e:
        raw_errors = e.errors()
        # When the policies list union (Policy | PolicyRuleRef) fails, Pydantic
        # v2 emits errors from BOTH arms.  The arm name appears as a string
        # segment in loc immediately after the list index, e.g.
        # ('policies', 0, 'Policy', 'when') vs ('policies', 0, 'PolicyRuleRef', '$ref').
        # We want to surface only the inline-model arm (Policy) errors when
        # both arms fail for the same element, so the caller sees exactly the
        # meaningful failures instead of ref-arm noise.
        # These string literals are the Pydantic v2 union-arm discriminators — the
        # class __name__s of Policy and PolicyRuleRef.  If either class is renamed,
        # update these constants to match or the dedup logic silently stops working.
        inline_arm = "Policy"
        ref_arm = "PolicyRuleRef"
        # Collect the element indices that have an inline-arm error.
        inline_arm_indices: set[int] = set()
        for err in raw_errors:
            loc = err.get("loc", ())
            if len(loc) >= 3 and isinstance(loc[1], int) and loc[2] == inline_arm:
                inline_arm_indices.add(loc[1])
        errors: list[PolicyValidationError] = []
        for err in raw_errors:
            loc = err.get("loc", ())
            # Skip ref-arm errors for items that also have an inline-arm error.
            if (
                len(loc) >= 3
                and isinstance(loc[1], int)
                and loc[2] == ref_arm
                and loc[1] in inline_arm_indices
            ):
                continue
            # Strip the union arm type-name segment from loc before path conversion.
            if len(loc) >= 3 and isinstance(loc[1], int) and loc[2] in (inline_arm, ref_arm):
                loc = (loc[0], loc[1]) + loc[3:]
            path = _loc_to_path(loc)
            msg = err.get("msg", "validation error")
            # ``Expr``'s recursive ``_validate_operand`` raises ``ValueError``
            # with messages already prefixed by their AST path
            # (``$.eq: eq does not accept null literals ...``). Pydantic v2
            # wraps the raise as ``"Value error, <original>"``, so the inner
            # path ends up embedded in ``msg`` instead of in ``loc``. Splice
            # the inner path onto the loc path so the client gets the full
            # ``$.policies[0].when.eq[1]`` form.
            inner_path, sep, inner_msg = _split_value_error_msg(msg)
            if sep:
                path = path + inner_path[1:] if inner_path != "$" else path
                msg = inner_msg
            errors.append(PolicyValidationError(path=path, message=msg))
        return PolicyValidationResponse(ok=False, errors=errors)
    except ValueError as e:
        # The Expr validator's ValueError is already path-prefixed
        # (``$.policies[0].when.eq[1]: <message>``). Split the prefix back
        # out so the client doesn't render the path twice. This branch
        # fires when the ValueError is raised outside Pydantic's own
        # validation context (defensive — the ``Expr`` raises currently
        # surface as the ValidationError branch above).
        text = str(e)
        path, sep, msg = text.partition(": ")
        return PolicyValidationResponse(
            ok=False,
            errors=[
                PolicyValidationError(
                    path=path if sep else "$",
                    message=msg if sep else text,
                )
            ],
        )


@router.get(
    "/{table_id}",
    response_model=TablePublic,
    summary="Get table metadata",
)
async def get_table(
    table_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> TablePublic:
    """Get table metadata by UUID (platform admin only)."""
    result = await ctx.db.execute(select(Table).where(Table.id == table_id))
    table = result.scalar_one_or_none()
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{table_id}' not found",
        )
    return TablePublic.model_validate(table)


@router.patch(
    "/{table_id}",
    response_model=TablePublic,
    summary="Update table",
)
async def update_table(
    table_id: UUID,
    data: TableUpdate,
    ctx: Context,
    user: CurrentSuperuser,
) -> TablePublic:
    """Update table metadata by ID (platform admin only).

    Solution-managed tables are read-only here: deploy owns schema + policies.
    Row DATA (documents) stays editable — that's runtime state (criterion 7).
    """
    await assert_entity_id_not_solution_managed(ctx.db, Table, table_id)
    if "policies" in data.model_fields_set:
        existing_table = (
            await ctx.db.execute(select(Table).where(Table.id == table_id))
        ).scalar_one_or_none()
        if existing_table is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table '{table_id}' not found",
            )
        try:
            await _validate_table_policy_claim_refs(
                ctx.db,
                existing_table.organization_id,
                data.policies,
                existing_table.solution_id,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            )

    repo = TableRepository(ctx.db, ctx.org_id, is_superuser=True)
    try:
        table = await repo.update_table(table_id, data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{table_id}' not found",
        )

    if "policies" in data.model_fields_set:
        # Subscribers re-read policies on a separate database connection when
        # they receive this event.  Commit first so they cannot observe the old
        # policy and incorrectly retain access under load.
        await ctx.db.commit()
        await publish_policy_changed(str(table.id))

    return TablePublic.model_validate(table)


@router.delete(
    "/{table_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete table",
)
async def delete_table(
    table_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
) -> None:
    """Delete a table and all its documents by ID (platform admin only)."""
    from shared.sdk_table_metadata import SDKTableMetadataError, delete_sdk_table

    try:
        success = await delete_sdk_table(
            ctx.db, table_id=table_id, org_id=ctx.org_id
        )
    except SDKTableMetadataError as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        ) from None

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Table '{table_id}' not found",
        )


# =============================================================================
# Document Endpoints
# =============================================================================


@router.post(
    "/{table_id}/documents",
    response_model=DocumentPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Insert a document",
)
async def insert_document(
    table_id: str,
    body: DocumentCreate,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentPublic:
    """Insert a new document into the table."""
    table = await get_table_or_404(ctx, table_id, scope=scope)
    await _assert_solution_write_targets_owned_table(ctx, table)
    try:
        doc = await insert_table_document(
            ctx.db,
            table,
            ctx.user,
            doc_id=body.id,
            data=body.data,
            created_by=body.created_by,
            updated_by=body.updated_by,
            upsert=body.upsert,
        )
    except TableWriteError as exc:
        raise _table_write_http_error(exc) from exc
    return DocumentPublic.model_validate(doc)


@router.post(
    "/{table_id}/documents/upsert",
    response_model=DocumentPublic,
    summary="Upsert a document by id",
)
async def upsert_document(
    table_id: str,
    body: DocumentUpsert,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentPublic:
    """Atomically upsert a document by id (single ``INSERT ... ON CONFLICT DO UPDATE``).

    On conflict the JSONB ``data`` column is **replaced**, not merged — use
    PATCH ``/{doc_id}`` for partial updates with merge semantics.

    The candidate row is policy-checked for ``create``; if a row already
    exists, ``update`` is additionally required on BOTH its pre-image and
    the replaced post-image. Any denial returns 403; the row is not written.

    NOTE: This route is declared BEFORE ``GET /{table_id}/documents/{doc_id}``
    so the literal ``/upsert`` segment matches first. Reversing the order
    binds ``doc_id="upsert"`` and the endpoint becomes unreachable.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)
    await _assert_solution_write_targets_owned_table(ctx, table)
    try:
        doc = await upsert_table_document(
            ctx.db,
            table,
            ctx.user,
            doc_id=body.id,
            data=body.data,
            created_by=body.created_by,
            updated_by=body.updated_by,
        )
    except TableWriteError as exc:
        raise _table_write_http_error(exc) from exc
    return DocumentPublic.model_validate(doc)


@router.get(
    "/{table_id}/documents/count",
    response_model=DocumentCountResponse,
    summary="Count documents",
)
async def count_documents(
    table_id: str,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentCountResponse:
    """Count documents in a table.

    Returns 404 if the table doesn't exist.

    NOTE: This route is declared BEFORE ``GET /{table_id}/documents/{doc_id}``
    so the literal ``/count`` segment matches first. Reversing the order makes
    FastAPI bind ``doc_id="count"`` and return 404, silently disabling the
    count endpoint.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)
    return await count_table_documents(ctx.db, table, ctx.user)


@router.get(
    "/{table_id}/documents/{doc_id}",
    response_model=DocumentPublic,
    summary="Get a document",
)
async def get_document(
    table_id: str,
    doc_id: str,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentPublic:
    """Get a document by ID."""
    table = await get_table_or_404(ctx, table_id, scope=scope)
    return await get_table_document(ctx.db, table, doc_id, ctx.user)


@router.patch(
    "/{table_id}/documents/{doc_id}",
    response_model=DocumentPublic,
    summary="Update a document",
)
async def update_document(
    table_id: str,
    doc_id: str,
    body: DocumentUpdate,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentPublic:
    """Update a document (partial update, merges with existing).

    The ``update`` policy must pass on BOTH the pre-image and the merged
    post-image — mutating a row into a value the caller could not write
    (e.g. retargeting ``organization_id``) is denied with 403 and the row
    is left untouched.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)
    await _assert_solution_write_targets_owned_table(ctx, table)
    try:
        doc = await update_table_document(
            ctx.db,
            table,
            ctx.user,
            doc_id=doc_id,
            data=body.data,
            updated_by=body.updated_by,
        )
    except TableWriteError as exc:
        raise _table_write_http_error(exc) from exc
    return DocumentPublic.model_validate(doc)


@router.delete(
    "/{table_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
)
async def delete_document(
    table_id: str,
    doc_id: str,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> None:
    """Delete a document."""
    table = await get_table_or_404(ctx, table_id, scope=scope)
    await _assert_solution_write_targets_owned_table(ctx, table)
    try:
        await delete_table_document(ctx.db, table, ctx.user, doc_id=doc_id)
    except TableWriteError as exc:
        raise _table_write_http_error(exc) from exc


@router.post(
    "/{table_id}/documents/query",
    response_model=DocumentListResponse,
    summary="Query documents",
)
async def query_documents(
    table_id: str,
    query_params: DocumentQuery,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentListResponse:
    """Query documents with filtering and pagination.

    Returns 404 if the table doesn't exist.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)
    return await query_table_documents(ctx.db, table, query_params, ctx.user)


@router.post(
    "/{table_id}/documents/batch",
    response_model=DocumentBatchCreateResponse,
    summary="Batch insert or upsert documents",
)
async def batch_documents(
    table_id: str,
    body: DocumentBatchCreate,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentBatchCreateResponse:
    """Insert, merge-upsert, or replace-upsert multiple documents."""
    table = await get_table_or_404(ctx, table_id, scope=scope)
    await _assert_explicit_scope_targets_table(ctx, table, scope)
    await _assert_solution_write_targets_owned_table(ctx, table)
    try:
        outcome = await batch_write_table_documents(
            ctx.db,
            table,
            ctx.user,
            items=[
                BatchDocumentInput(
                    id=item.id,
                    data=item.data,
                    created_by=item.created_by,
                    updated_by=item.updated_by,
                )
                for item in body.documents
            ],
            mode=body.effective_write_mode,
        )
    except TableWriteError as exc:
        raise _table_write_http_error(exc) from exc

    return DocumentBatchCreateResponse(
        inserted=outcome.inserted,
        errors=[
            {"id": conflict.id, "error": "Document already exists"}
            for conflict in outcome.insert_conflicts
        ],
        documents=(
            [DocumentPublic.model_validate(doc) for doc in outcome.ordered_documents]
            if body.return_documents
            else []
        ),
    )


@router.post(
    "/{table_id}/documents/batch-delete",
    response_model=DocumentBatchDeleteResponse,
    summary="Batch delete documents by ID",
)
async def batch_delete_documents(
    table_id: str,
    body: DocumentBatchDeleteRequest,
    ctx: Context,
    scope: str | None = Query(
        None,
        description="Target organization scope: 'global' or org UUID. Defaults to caller's home org. Provider admins only for non-self orgs.",
    ),
) -> DocumentBatchDeleteResponse:
    """Delete multiple documents by ID.

    Skips IDs that don't exist. All-or-nothing on policy denials: any
    denied row aborts the whole batch with a 403 listing every denied index.
    """
    table = await get_table_or_404(ctx, table_id, scope=scope)
    await _assert_solution_write_targets_owned_table(ctx, table)
    try:
        outcome = await batch_delete_table_documents(
            ctx.db, table, ctx.user, ids=list(body.ids)
        )
    except TableWriteError as exc:
        raise _table_write_http_error(exc) from exc
    return DocumentBatchDeleteResponse(
        deleted=outcome.deleted, deleted_ids=outcome.deleted_ids
    )
