"""Shared helpers for solution-scoped storage declarations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import ExecutionContext
from src.core.org_filter import resolve_target_org
from src.models.orm.applications import Application
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.repositories.tables import TableRepository


@dataclass(frozen=True)
class FileTier:
    name: Literal["solution", "org", "global"]
    scope: str
    organization_id: UUID | None
    solution_id: UUID | None


def parse_ctx_solution_id(ctx) -> UUID | None:
    """Parse ``ctx.solution_id`` (set by auth) into a UUID, or None.

    THE single parse point — routers must not re-implement this
    (tests/unit/test_solution_scope_enforcement.py)."""
    raw = getattr(ctx, "solution_id", None)
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


def is_engine_user(user) -> bool:
    """True when the request authenticates as the engine sentinel.

    Mirrors ``src/routers/tables.py``. Only engine requests may attest
    ``caller_solution_id`` — direct callers' values are ignored.
    """
    from src.core.constants import SYSTEM_USER_UUID

    try:
        return user.user_id == SYSTEM_USER_UUID
    except AttributeError:
        return False


def is_service_principal(user) -> bool:
    """True for renewable service-scoped credentials (mint_service_token).

    The engine sentinel as subject with a service identity attached and
    WITHOUT superuser: an org-scoped service attempt calling the API on its
    own behalf. Workflow engine tokens (superuser, no service claims) and
    ordinary users are both False. Routers admit service principals only to
    explicitly service-safe endpoints with org confinement enforced there.
    """
    from src.core.constants import SYSTEM_USER_UUID

    try:
        return (
            user.user_id == SYSTEM_USER_UUID
            and not user.is_superuser
            and user.service_id is not None
        )
    except AttributeError:
        return False


async def resolve_trustworthy_caller(db: AsyncSession, ctx) -> UUID | None:
    """SPIKE: the caller's OWN install, from a trustworthy source only.

    Precedence: the SIGNED engine claims on the execution-scoped token
    (mint_engine_token — unforgeable without SECRET_KEY; None solution =
    _repo execution, i.e. outside) > the DB-backed app-header install >
    the SDK-attested ``?caller_solution=``/body value on engine-sub
    requests. Everyone else → None (outside any install). Used for the
    inbound own-call bypass. Request-supplied caller ids are NEVER trusted
    on their own — Codex review P1.
    """
    engine_execution_id = getattr(getattr(ctx, "user", None), "engine_execution_id", None)
    if engine_execution_id is not None:
        raw = getattr(ctx.user, "engine_solution_id", None)
        if raw is None:
            return None
        try:
            return UUID(str(raw))
        except (ValueError, AttributeError, TypeError):
            return None
    if getattr(ctx, "app_id", None):
        try:
            from uuid import UUID as _UUID

            app_uuid = _UUID(str(ctx.app_id))
        except ValueError:
            app_uuid = None
        if app_uuid is not None:
            row = (
                await db.execute(
                    select(Application.solution_id).where(
                        Application.id == app_uuid
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                return row
    raw = getattr(ctx, "caller_solution_id", None)
    if raw is not None and is_engine_user(ctx.user):
        try:
            return UUID(str(raw))
        except (ValueError, AttributeError, TypeError):
            return None
    return None


async def check_inbound_allowed(
    db: AsyncSession,
    target_id: UUID,
    caller_id: UUID | None,
) -> bool:
    """SPIKE: may ``caller_id`` reach ``target_id``'s resources?

    Own-install calls (caller == target) always pass. Otherwise the target's
    ``allow_inbound_access`` decides. Inactive/missing targets deny (callers
    see 404, never a reason).
    """
    if caller_id is not None and caller_id == target_id:
        return True
    row = (
        await db.execute(
            select(Solution.status, Solution.allow_inbound_access).where(
                Solution.id == target_id
            )
        )
    ).one_or_none()
    if row is None:
        return False
    status, allow_inbound = row._tuple()
    return bool(status == "active" and allow_inbound)


class SolutionInboundDenied(Exception):
    """An explicitly targeted install refused inbound access (sealed,
    inactive, or unknown ref).

    Distinct from "no scope" (None): routers must 404 WITHOUT shared
    fallback — a denied target must never execute a loose same-path
    workflow (Codex review P1).
    """

    def __init__(self, ref: str | None = None) -> None:
        super().__init__(ref or "solution inbound denied")
        self.ref = ref


async def get_active_solution(db: AsyncSession, solution_id: UUID) -> Solution | None:
    solution = await db.get(Solution, solution_id)
    if solution is None or solution.status != "active":
        return None
    return solution


async def resolve_solution_ref(
    db: AsyncSession,
    ref: str | None,
    target_org_id: UUID | None,
) -> UUID | None:
    """SPIKE: resolve a per-call ``solution=`` ref inside the resolved org scope.

    ``ref`` is a solution install UUID or a slug/name. UUIDs pass through
    (downstream resolvers + org gates enforce reachability, as today — keeps
    the deprecated body-UUID compat path unchanged). Slugs/names resolve to
    the active install in ``target_org_id`` (None = global install); no extra
    permission check since the scope resolver already gated the scope.
    """
    if not ref:
        return None
    raw = str(ref).strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        pass
    stmt = select(Solution).where(
        Solution.status == "active",
        Solution.organization_id.is_(None)
        if target_org_id is None
        else Solution.organization_id == target_org_id,
    )
    rows = (await db.execute(stmt)).scalars().all()
    for sol in rows:
        if sol.slug == raw:
            return sol.id
    for sol in rows:
        if sol.name == raw:
            return sol.id
    return None


async def solution_allows_global(db: AsyncSession, solution_id: UUID) -> bool:
    # Select only the scalar needed at this request boundary. Loading the
    # Solution ORM also selectin-loads its file locations and connection schema,
    # turning this boolean check into three queries on every execution.
    result = await db.execute(
        select(Solution.allow_outbound_access).where(Solution.id == solution_id)
    )
    return bool(result.scalar_one_or_none())


async def solution_declares_file_location(
    db: AsyncSession,
    solution_id: UUID,
    location: str,
) -> bool:
    result = await db.execute(
        select(SolutionFileLocation.id).where(
            SolutionFileLocation.solution_id == solution_id,
            SolutionFileLocation.location == location,
        )
    )
    return result.scalar_one_or_none() is not None


async def solution_declares_table_name(
    db: AsyncSession,
    solution_id: UUID,
    name: str,
) -> bool:
    result = await db.execute(
        select(Table.id).where(
            Table.solution_id == solution_id,
            Table.name == name,
        )
    )
    return result.scalar_one_or_none() is not None


async def solution_context_id(
    db: AsyncSession,
    ctx: ExecutionContext,
) -> UUID | None:
    """Resolve the active install id from request context.

    Auth populates ``ctx.solution_id`` for both ``?solution=`` and solution app
    calls via ``X-Bifrost-App``. The app-id fallback keeps older call sites and
    unit tests that construct contexts manually on the same resolver path.
    """
    ctx_scope = parse_ctx_solution_id(ctx)
    if ctx_scope is not None:
        return ctx_scope

    # SPIKE: slug/name ref in ?solution= (auth passes it through raw) — needs
    # target_org to resolve, so this path stays UUID-only; use
    # resolve_effective_solution_id() where the scope is known.
    raw = getattr(ctx, "solution_id", None)
    if raw is not None and str(raw).strip():
        try:
            UUID(str(raw))
        except ValueError:
            pass  # slug — resolved by caller with scope, not here.
    if not ctx.app_id:
        return None
    try:
        app_uuid = UUID(str(ctx.app_id))
    except ValueError:
        return None

    return (
        await db.execute(
            select(Application.solution_id).where(Application.id == app_uuid)
        )
    ).scalar_one_or_none()


async def resolve_effective_solution_id(
    db: AsyncSession,
    ctx,
    target_org_id: UUID | None,
) -> UUID | None:
    """SPIKE: request-scoped install id for tables/files.

    ``ctx.solution_id`` holds the raw ``?solution=`` value (UUID or slug/name —
    auth passes slugs through). UUIDs keep today's behavior; slugs resolve via
    ``resolve_solution_ref`` inside the already-resolved ``target_org_id``.
    """
    raw = getattr(ctx, "solution_id", None)
    if raw is not None and str(raw).strip():
        try:
            return UUID(str(raw))
        except ValueError:
            resolved = await resolve_solution_ref(db, str(raw), target_org_id)
            if resolved is not None:
                return resolved
    return await solution_context_id(db, ctx)


async def derive_execution_solution_scope(
    db: AsyncSession,
    ctx,
    *,
    solution_id: str | None,
    form_id: str | None,
    app_id: str | None,
    target_org_id: UUID | None = None,
    caller_solution_id: str | None = None,
) -> UUID | None:
    """Resolve the calling install's scope for workflow execution.

    THE canonical derivation for /api/workflows/execute. Precedence:
    request context (auth already resolved ?solution= / X-Bifrost-App —
    the same signal tables/files scope by) > body solution_id (a Solution
    form/agent that knows its own install, or a per-call ``solution=`` SDK
    target) > form_id (Form.solution_id) > app_id (Application.solution_id).
    The body fields are DEPRECATED compatibility inputs — live SDKs still
    send them; removal requires a MIN_CLI_VERSION raise. A bad/foreign/
    missing reference yields None → no narrowing (the path ref resolves the
    _repo/ row, or 404s for a scoped caller). Each source is client-supplied;
    the resolver's own org gate (cascade scope) prevents a foreign scope from
    reaching another org's workflow.

    SPIKE: every resolved install passes the inbound gate — own-install
    callers always pass, otherwise the target's ``allow_inbound_access``
    decides. An EXPLICIT ref (``?solution=`` UUID or body ``solution_id``)
    that is unknown, inactive, or sealed raises :class:`SolutionInboundDenied`
    (routers 404 WITHOUT shared fallback — a denied target must never execute
    a loose same-path workflow). Unset/compat sources (app header, form,
    app) keep the old None semantics (no narrowing).

    Raises:
        SolutionInboundDenied: an explicit ref did not resolve to a
            reachable install.
    """
    from src.models.orm.forms import Form

    # Precedence (unchanged): ctx (?solution=/app header) > body > form > app.
    # Body accepts UUID (passthrough, as today) or slug/name (SPIKE: resolved
    # inside target_org_id). SDK per-call override flows through body with
    # ctx unset, so it resolves; direct HTTP callers with both keep ctx-wins.
    target: UUID | None = None
    explicit = False
    ctx_scope = await solution_context_id(db, ctx)
    if ctx_scope is not None:
        target = ctx_scope
        # ?solution= carries an explicit UUID (SDK-forwarded own install or a
        # direct targeting attempt) while the app-header path carries the
        # caller's own install implicitly. Gate the former; the latter passes
        # via the own-call bypass below.
        explicit = parse_ctx_solution_id(ctx) is not None
    else:
        raw_ctx_solution = getattr(ctx, "solution_id", None)
        if raw_ctx_solution:
            # ?solution= slug/name (auth passes it through raw): resolve
            # inside the target org like a body ref. Unresolvable garbage
            # falls through to the compat sources below (pre-spike
            # behavior); a resolved install is always explicit.
            slug_target = await resolve_solution_ref(
                db, str(raw_ctx_solution), target_org_id
            )
            if slug_target is not None:
                target = slug_target
                explicit = True
        if target is None and solution_id:
            target = await resolve_solution_ref(db, solution_id, target_org_id)
            explicit = target is not None
            if solution_id and target is None:
                # Explicit body ref that resolves nowhere: denial, not
                # fallback (a dangling id must not execute a loose workflow).
                raise SolutionInboundDenied(solution_id)
        if target is None and form_id:
            try:
                form_uuid = UUID(form_id)
            except ValueError:
                return None
            target = (
                await db.execute(select(Form.solution_id).where(Form.id == form_uuid))
            ).scalar_one_or_none()
        if target is None and app_id:
            try:
                app_uuid = UUID(app_id)
            except ValueError:
                return None
            target = (
                await db.execute(
                    select(Application.solution_id).where(Application.id == app_uuid)
                )
            ).scalar_one_or_none()
    if target is None:
        if explicit:
            raise SolutionInboundDenied(solution_id)
        return None
    caller = await resolve_trustworthy_caller(db, ctx)
    if caller is None and caller_solution_id and is_engine_user(ctx.user):
        try:
            caller = UUID(str(caller_solution_id))
        except (ValueError, AttributeError, TypeError):
            caller = None
    if await check_inbound_allowed(db, target, caller):
        return target
    if explicit:
        raise SolutionInboundDenied(solution_id)
    return None


async def resolve_solution_table_by_name(
    db: AsyncSession,
    ctx: ExecutionContext,
    name: str,
    target_org_id: UUID | None,
) -> Table | None:
    """Resolve a table name from solution context.

    Tier order:
    1. the solution-owned table for this install, when deployed under ``name``;
    2. for open solutions only, the ordinary org/global _repo cascade.

    The fallback table, when returned, is still a shared _repo table with
    ``solution_id IS NULL``. Callers that mutate documents must reject that case.
    """
    solution_id = await resolve_effective_solution_id(db, ctx, target_org_id)
    if solution_id is None:
        return None

    # SPIKE inbound gate: own-install callers always pass, otherwise the
    # target's allow_inbound_access decides. Denied → None (404 downstream).
    caller = await resolve_trustworthy_caller(db, ctx)
    if not await check_inbound_allowed(db, solution_id, caller):
        return None

    solution = await get_active_solution(db, solution_id)
    if solution is None:
        return None

    own_stmt = select(Table).where(
        Table.name == name,
        Table.solution_id == solution_id,
    )
    # Bypass = is_platform_admin OR is_provider_org (repositories/README.md):
    # provider-org members (portal-hopping platform staff) reach any org's
    # install-owned table, same as platform admins. Row access is still decided
    # by the client org's table/file policies after resolution.
    if not (ctx.user.is_superuser or ctx.user.is_provider_org):
        own_stmt = own_stmt.where(
            or_(
                Table.organization_id == target_org_id,
                Table.organization_id.is_(None),
            )
        )
    own = (await db.execute(own_stmt)).scalar_one_or_none()
    if own is not None:
        return own

    if not solution.allow_outbound_access:
        return None

    repo = TableRepository(
        db,
        target_org_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_superuser,
        is_external=ctx.user.is_external,
    )
    return await repo.get_by_name(name)


async def file_read_tiers(
    db: AsyncSession,
    ctx: ExecutionContext,
    location: str,
    requested_scope: str | None,
) -> list[FileTier]:
    """Return candidate storage tiers for file read/list/exists operations."""
    if ctx.solution_id is None:
        org_id = _file_org_id(ctx, location, requested_scope)
        return [
            FileTier(
                "global" if org_id is None else "org",
                _storage_scope(org_id),
                org_id,
                None,
            )
        ]

    if location == "workspace":
        raise ValueError("workspace is not available in solution file context")

    solution_id = parse_ctx_solution_id(ctx)
    if solution_id is None:
        # SPIKE: slug/name in ?solution= — resolve inside the requested scope.
        raw = str(getattr(ctx, "solution_id", ""))
        solution_id = await resolve_solution_ref(
            db, raw, _file_org_id(ctx, location, requested_scope)
        )
    if solution_id is None:
        return []
    # SPIKE inbound gate (same rule as tables): own-install callers pass,
    # otherwise allow_inbound_access decides. Denied → no tiers (404 downstream).
    caller = await resolve_trustworthy_caller(db, ctx)
    if not await check_inbound_allowed(db, solution_id, caller):
        return []
    solution = await db.get(Solution, solution_id)
    if solution is None:
        return []

    tiers = [
        FileTier(
            "solution",
            str(solution_id),
            solution.organization_id,
            solution_id,
        )
    ]
    if solution.allow_outbound_access:
        if solution.organization_id is not None:
            tiers.append(
                FileTier(
                    "org",
                    str(solution.organization_id),
                    solution.organization_id,
                    None,
                )
            )
        tiers.append(FileTier("global", "global", None, None))
    return tiers


def _file_org_id(
    ctx: ExecutionContext,
    location: str,
    requested_scope: str | None,
) -> UUID | None:
    if location == "workspace":
        return None
    return resolve_target_org(ctx.user, requested_scope, ctx.org_id)


def _storage_scope(org_id: UUID | None) -> str:
    return str(org_id) if org_id is not None else "global"
