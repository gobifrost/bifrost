"""Shared FastAPI wiring for the main API app and the worker-local SDK app.

The main API app (``src.main``) and the worker-local engine SDK app
(``src.services.execution.worker_sdk_http``) serve the **same** SDK route
objects. They must therefore share the same global exception mapping and the
same request-context middleware, so an SDK call made over the worker's Unix
socket is attributed and errored identically to one made over HTTP.

This module is the single home for that wiring: :func:`register_exception_handlers`
and :func:`install_request_context_middleware`.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError, NoResultFound, OperationalError

from src.models.contracts.common import ErrorResponse

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Register the platform's global exception handlers on ``app``.

    Consistent ``ErrorResponse`` bodies for validation, database, timeout,
    and unexpected failures. Handlers are registered in order of specificity
    (most specific first).
    """

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Request validation errors (bad input) → 422 with concise messages."""
        messages = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"] if p != "body")
            messages.append(f"{field}: {err['msg']}")
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="validation_error",
                message="; ".join(messages),
            ).model_dump(),
        )

    @app.exception_handler(PydanticValidationError)
    async def pydantic_validation_handler(
        request: Request, exc: PydanticValidationError
    ) -> JSONResponse:
        """Pydantic model validation errors → 422."""
        errors = exc.errors()
        # Extract field names and messages for user-friendly output
        field_errors = {
            ".".join(str(loc) for loc in e["loc"]): e["msg"] for e in errors
        }
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="validation_error",
                message="Validation failed",
                details={"fields": field_errors},
            ).model_dump(),
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(
        request: Request, exc: IntegrityError
    ) -> JSONResponse:
        """Database constraint violations → 409."""
        detail = str(exc.orig) if exc.orig else str(exc)

        if "unique" in detail.lower() or "duplicate" in detail.lower():
            message = "Resource already exists"
        elif "foreign key" in detail.lower():
            message = "Referenced resource not found"
        else:
            message = "Database constraint violation"

        logger.warning(f"IntegrityError: {detail}")
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                error="conflict",
                message=message,
            ).model_dump(),
        )

    @app.exception_handler(NoResultFound)
    async def no_result_handler(
        request: Request, exc: NoResultFound
    ) -> JSONResponse:
        """Query returned no results → 404."""
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error="not_found",
                message="Resource not found",
            ).model_dump(),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(
        request: Request, exc: ValueError
    ) -> JSONResponse:
        """ValueError from validation → 422."""
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="validation_error",
                message=str(exc),
            ).model_dump(),
        )

    @app.exception_handler(asyncio.TimeoutError)
    async def timeout_handler(
        request: Request, exc: asyncio.TimeoutError
    ) -> JSONResponse:
        """Timeout errors → 504."""
        logger.warning(f"Timeout error on {request.method} {request.url.path}")
        return JSONResponse(
            status_code=504,
            content=ErrorResponse(
                error="timeout",
                message="Operation timed out",
            ).model_dump(),
        )

    @app.exception_handler(OperationalError)
    async def operational_error_handler(
        request: Request, exc: OperationalError
    ) -> JSONResponse:
        """Database connection issues → 503."""
        logger.error(f"Database operational error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error="service_unavailable",
                message="Service temporarily unavailable",
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Catch-all for unhandled exceptions → 500 with safe message."""
        logger.error(
            f"Unhandled exception on {request.method} {request.url.path}: {exc}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="internal_error",
                message="An unexpected error occurred",
            ).model_dump(),
        )


def install_request_context_middleware(app: FastAPI) -> None:
    """Install the request-scoped context middleware on ``app``.

    Sets the watch session id, the request user attribution, and the audit
    :class:`ActorContext` for every request. The token is decoded once and the
    actor is built with :func:`src.core.request_actor.actor_from_token_payload`,
    so HTTP and worker-local (Unix socket) requests attribute audit events
    identically. Unauthenticated requests still get an anonymous actor so
    failed logins are recorded with network metadata.

    Also stashes the request's raw ASGI ``scope`` dict (see
    :func:`src.services.audit_context.set_request_scope`). Starlette's router
    mutates that same dict in place with ``scope["route"]`` once it matches a
    route — a fact that holds regardless of *how* a route reached this app
    (``include_router`` on the main app, or spliced directly into
    ``app.router.routes`` by the worker-local SDK socket app), since it is
    part of the public ASGI routing contract, not a FastAPI/Starlette
    implementation detail. ``emit_audit()`` reads ``scope["route"]`` at
    emission time to resolve the catalog operation id for the audit row;
    this middleware also reads it (after ``call_next`` returns, once routing
    has necessarily happened) to feed the workflow-operation-usage counter
    for engine-token requests carrying an ``engine_workflow_id`` claim.
    """
    from shared import access_checks
    from src.core.rate_limit import get_client_ip
    from src.core.request_actor import (
        actor_from_token_payload,
        resolve_self_reported_surface,
    )
    from src.core.request_context import (
        RequestUser,
        set_request_session_id,
        set_request_user,
    )
    from src.core.security import decode_token
    from src.services.audit_context import (
        ActorContext,
        clear_actor,
        clear_request_scope,
        set_actor,
        set_request_scope,
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        # Set watch session ID from header
        set_request_session_id(request.headers.get("x-bifrost-watch-session"))

        # Parse token once; use for both request_user and audit actor contexts.
        payload = None
        try:
            token = None
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            elif "access_token" in request.cookies:
                token = request.cookies["access_token"]
            if token:
                payload = decode_token(token, expected_type="access")
            if payload:
                user_id = payload.get("sub", "")
                user_name = (
                    payload.get("name") or payload.get("email") or user_id
                )
                set_request_user(
                    RequestUser(user_id=user_id, user_name=user_name)
                )
            else:
                set_request_user(None)
        except Exception:
            payload = None
            set_request_user(None)

        # ``request.client`` is None on a worker-local Unix-socket request;
        # record no IP rather than the sentinel "unknown".
        ip_address = None if request.client is None else get_client_ip(request)
        user_agent = request.headers.get("user-agent")
        surface_header = request.headers.get("x-bifrost-surface")

        actor = actor_from_token_payload(
            payload,
            ip_address=ip_address,
            user_agent=user_agent,
            surface_header=surface_header,
        )
        if actor is None:
            # No token (or an undecodable one): still set an anonymous actor
            # so unauthenticated events (e.g. failed logins) capture network
            # metadata.
            actor = ActorContext(
                user_id=None,
                organization_id=None,
                ip_address=ip_address,
                user_agent=user_agent,
                surface=resolve_self_reported_surface(surface_header),
            )
        actor_token = set_actor(actor)
        scope_token = set_request_scope(request.scope)
        # Report-only access checks must never affect the request: a token
        # whose lineage claims cannot be read is simply not judged.
        try:
            checks_token = access_checks.start_collecting(payload)
        except ValueError:
            logger.warning("access checks skipped: unreadable lineage claims")
            checks_token = None
        collector = access_checks.current()
        # Attribution only (see mint_engine_token docstring): feeds the
        # workflow-operation-usage counter below, never read for authorization.
        engine_workflow_id = payload.get("engine_workflow_id") if payload else None

        try:
            response = await call_next(request)
            # Routing has necessarily happened by now, so request.scope["route"]
            # (if any route matched) is populated.
            if engine_workflow_id:
                route = request.scope.get("route")
                if route is not None:
                    operation_key = (
                        getattr(route, "operation_id", None)
                        or f"{request.method} {route.path}"
                    )
                    await _record_workflow_operation_usage(
                        engine_workflow_id, operation_key
                    )
            if collector is not None:
                await _judge_access_checks(request, collector)
        finally:
            access_checks.stop_collecting(checks_token)
            # Reset context after request
            set_request_user(None)
            set_request_session_id(None)
            clear_actor(actor_token)
            clear_request_scope(scope_token)
        return response


async def _judge_access_checks(request: Request, collector) -> None:
    """Judge and write a run's report-only access checks (never raises).

    Runs after ``call_next`` returns, so the response is already decided;
    see ``shared.access_checks``.
    """
    from src.core.database import get_db_context
    from src.services import access_check_writer

    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    operation = getattr(route, "operation_id", None) or f"{request.method} {path}"
    try:
        async with get_db_context() as db:
            await access_check_writer.flush(
                db, collector, operation=operation, route=(request.method, path)
            )
    except Exception:
        logger.warning("access checks not written (operation=%s)", operation, exc_info=True)


async def _record_workflow_operation_usage(workflow_id: str, operation_key: str) -> None:
    """Best-effort daily usage counter for one workflow's engine-token calls.

    Attribution only: records which catalog operations (reads included) a
    workflow's SDK calls actually touch, for a later "what does this
    workflow actually need" pass — never read for authorization. Flushed
    into the durable ``workflow_operation_usage`` table by
    src/jobs/schedulers/workflow_operation_usage_flush.py every 15 minutes.

    Fire-and-forget: a Redis error is logged at debug and never fails or
    slows the request it would otherwise just tag.
    """
    from datetime import datetime, timezone

    from src.core.redis_client import get_redis_client

    try:
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"bifrost:wf_usage:{day}"
        field = f"{workflow_id}|{operation_key}"
        redis_conn = await get_redis_client()._get_redis()
        pipe = redis_conn.pipeline(transaction=False)
        pipe.hincrby(key, field, 1)
        pipe.expire(key, 60 * 60 * 24 * 3)
        await pipe.execute()
    except Exception as exc:
        # Attribution counter only — a Redis outage must never fail or slow
        # the request that would otherwise just be tagged.
        logger.debug("workflow operation usage increment failed: %s", exc)
