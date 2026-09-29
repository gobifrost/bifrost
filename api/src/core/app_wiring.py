"""Shared FastAPI wiring for the main API app and the worker-local SDK app.

The main API app (``src.main``) and the worker-local engine SDK app
(``src.services.execution.worker_sdk_http``) serve the **same** SDK route
objects. They must therefore share the same global exception mapping and the
same request-context middleware, so an SDK call made over the worker's Unix
socket is attributed and errored identically to one made over HTTP.

This module is the single home for that wiring: :func:`register_exception_handlers`,
:func:`install_request_context_middleware`, and :func:`install_operation_id_capture`.
"""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError, NoResultFound, OperationalError

from src.models.contracts.common import ErrorResponse

logger = logging.getLogger(__name__)

# The current request's engine-token ``engine_workflow_id`` claim (attribution
# only), read by _capture_operation_id() to feed the workflow-operation-usage
# counter. Set by the request-context middleware; not part of ActorContext
# since it is never used for audit attribution, only for that counter.
_engine_workflow_id: ContextVar[str | None] = ContextVar(
    "engine_workflow_id", default=None
)


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
    """
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
        set_actor,
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
        # Attribution only (see mint_engine_token docstring): feeds the
        # workflow-operation-usage counter in _capture_operation_id, never
        # read for authorization.
        engine_workflow_token = _engine_workflow_id.set(
            payload.get("engine_workflow_id") if payload else None
        )

        try:
            response = await call_next(request)
        finally:
            # Reset context after request
            set_request_user(None)
            set_request_session_id(None)
            clear_actor(actor_token)
            _engine_workflow_id.reset(engine_workflow_token)
        return response


def install_operation_id_capture(app: FastAPI) -> None:
    """Stamp each request's matched-route catalog operation id onto the actor.

    ``request.scope["route"]`` is only populated once Starlette has matched
    a route — i.e. after ``install_request_context_middleware``'s outer HTTP
    middleware has already set the actor, and after it would already be too
    late for a plain "before call_next" middleware step to see it. A FastAPI
    dependency runs at the right time (after routing, before the endpoint
    body), but a *global* ``dependencies=[...]`` list is only merged into a
    route's ``Dependant`` when that route is registered through
    ``include_router``/``add_api_route``. The worker-local SDK app
    (``worker_sdk_http.py``) instead splices already-built ``APIRoute``
    objects straight into ``app.router.routes``, bypassing that merge, so a
    global dependency would silently miss every worker-local SDK route.

    Mutating each route's already-built ``Dependant.dependencies`` list
    directly — after every route has been added to ``app`` — works
    uniformly for both apps regardless of how a given route got there: the
    list is read fresh at each request, not frozen at route-construction
    time (verified: this is the same object the request handler already
    holds a reference to).

    Call once, after the last route has been added to ``app``.
    """
    from fastapi.dependencies.utils import get_dependant
    from fastapi.routing import APIRoute

    for route in app.routes:
        if isinstance(route, APIRoute):
            route.dependant.dependencies.insert(
                0, get_dependant(path=route.path, call=_capture_operation_id)
            )


async def _capture_operation_id(request: Request) -> None:
    """Stamp the matched route's catalog operation id onto the audit actor.

    Runs as a synthetic dependency on every route (see
    :func:`install_operation_id_capture`), after routing has matched
    ``request.scope["route"]`` but before the endpoint body executes.
    Routes without a catalog operation id (no ``operation_route()``
    binding) leave the actor's ``operation_id`` unset.

    Also feeds the per-workflow catalog-operation-usage counter (Part 2 of
    R2a-2) for requests authenticated by an engine token that carries an
    ``engine_workflow_id`` claim.
    """
    from dataclasses import replace

    from src.services.audit_context import current_actor, set_actor

    route = request.scope.get("route")
    operation_id = getattr(route, "operation_id", None) if route is not None else None

    if operation_id:
        actor = current_actor()
        if actor is not None:
            set_actor(replace(actor, operation_id=operation_id))

    workflow_id = _engine_workflow_id.get()
    if workflow_id and route is not None:
        operation_key = operation_id or f"{request.method} {route.path}"
        await _record_workflow_operation_usage(workflow_id, operation_key)


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
