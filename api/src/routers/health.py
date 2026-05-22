"""
Health Check Router

Provides endpoints for monitoring application health.
"""

import asyncio
from collections.abc import Awaitable
from datetime import datetime, timezone
from typing import cast

import aio_pika
import redis.asyncio as redis
from aiobotocore.session import get_session
from aio_pika.abc import AbstractRobustConnection
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.core.database import get_db
from shared.version import get_version

router = APIRouter(prefix="/health", tags=["health"])

CHECK_TIMEOUT_SECONDS = 2.0
ComponentStatus = dict[str, str]
_rabbitmq_health_connection: AbstractRobustConnection | None = None
_rabbitmq_health_connection_url: str | None = None
_rabbitmq_health_lock = asyncio.Lock()


class HealthCheck(BaseModel):
    """Health check response model."""
    status: str
    timestamp: datetime
    version: str = Field(default_factory=get_version)
    environment: str


class DetailedHealthCheck(BaseModel):
    """Detailed health check with component status."""
    status: str
    timestamp: datetime
    version: str = Field(default_factory=get_version)
    environment: str
    components: dict[str, dict]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _component(status: str, type_: str, error: str | None = None) -> ComponentStatus:
    component = {"status": status, "type": type_}
    if error:
        component["error"] = error
    return component


def _error_name(exc: BaseException) -> str:
    return exc.__class__.__name__


async def _checked_component(
    name: str,
    type_: str,
    check: Awaitable[object],
) -> tuple[str, ComponentStatus]:
    try:
        await asyncio.wait_for(check, timeout=CHECK_TIMEOUT_SECONDS)
        return name, _component("healthy", type_)
    except Exception as exc:
        return name, _component("unhealthy", type_, _error_name(exc))


async def check_database(db: AsyncSession) -> tuple[str, ComponentStatus]:
    return await _checked_component("database", "postgresql", db.execute(text("SELECT 1")))


async def check_redis(settings: Settings) -> tuple[str, ComponentStatus]:
    async def ping() -> None:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await cast(Awaitable[object], client.ping())
        finally:
            await client.aclose()

    return await _checked_component("redis", "redis", ping())


def _rabbitmq_connection_is_open(connection: AbstractRobustConnection) -> bool:
    return not bool(connection.is_closed)


async def close_rabbitmq_health_connection() -> None:
    global _rabbitmq_health_connection, _rabbitmq_health_connection_url

    connection = _rabbitmq_health_connection
    _rabbitmq_health_connection = None
    _rabbitmq_health_connection_url = None
    if connection is not None and _rabbitmq_connection_is_open(connection):
        await connection.close()


async def _get_rabbitmq_health_connection(settings: Settings) -> AbstractRobustConnection:
    global _rabbitmq_health_connection, _rabbitmq_health_connection_url

    connection = _rabbitmq_health_connection
    if (
        connection is not None
        and _rabbitmq_health_connection_url == settings.rabbitmq_url
        and _rabbitmq_connection_is_open(connection)
    ):
        return connection

    async with _rabbitmq_health_lock:
        connection = _rabbitmq_health_connection
        if (
            connection is not None
            and _rabbitmq_health_connection_url == settings.rabbitmq_url
            and _rabbitmq_connection_is_open(connection)
        ):
            return connection

        if connection is not None and _rabbitmq_connection_is_open(connection):
            await connection.close()

        _rabbitmq_health_connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        _rabbitmq_health_connection_url = settings.rabbitmq_url
        return _rabbitmq_health_connection


async def check_rabbitmq(settings: Settings) -> tuple[str, ComponentStatus]:
    async def open_channel() -> None:
        connection = await _get_rabbitmq_health_connection(settings)
        try:
            channel = await connection.channel()
            await channel.close()
        except Exception:
            await connection.close()
            global _rabbitmq_health_connection, _rabbitmq_health_connection_url
            if _rabbitmq_health_connection is connection:
                _rabbitmq_health_connection = None
                _rabbitmq_health_connection_url = None
            raise

    return await _checked_component("rabbitmq", "rabbitmq", open_channel())


async def check_s3(settings: Settings) -> tuple[str, ComponentStatus]:
    if not settings.s3_configured:
        return "s3", _component("not_configured", "s3")

    async def head_bucket() -> None:
        session = get_session()
        async with session.create_client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        ) as client:
            await cast(Awaitable[object], client.head_bucket(Bucket=settings.s3_bucket))

    return await _checked_component("s3", "s3", head_bucket())


async def build_health_components(
    db: AsyncSession,
    settings: Settings,
) -> dict[str, ComponentStatus]:
    checks = await asyncio.gather(
        check_database(db),
        check_redis(settings),
        check_rabbitmq(settings),
        check_s3(settings),
    )
    return dict(checks)


def _overall_status(components: dict[str, ComponentStatus]) -> str:
    if any(component.get("status") == "unhealthy" for component in components.values()):
        return "unhealthy"
    return "healthy"


async def _readiness_response(
    response: Response,
    db: AsyncSession,
) -> DetailedHealthCheck:
    settings = get_settings()
    components = await build_health_components(db, settings)
    overall_status = _overall_status(components)
    if overall_status == "unhealthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return DetailedHealthCheck(
        status=overall_status,
        timestamp=_now(),
        environment=settings.environment,
        components=components,
    )


@router.get("", response_model=HealthCheck)
async def health_check() -> HealthCheck:
    """
    Basic health check endpoint.

    Returns:
        Basic health status
    """
    settings = get_settings()
    return HealthCheck(
        status="healthy",
        timestamp=_now(),
        environment=settings.environment,
    )


@router.get("/live", response_model=HealthCheck)
async def live_health_check() -> HealthCheck:
    """
    Liveness check endpoint.

    Returns healthy when the API process can respond.
    """
    return await health_check()


@router.get("/ready", response_model=DetailedHealthCheck)
async def ready_health_check(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> DetailedHealthCheck:
    """
    Readiness check for core API serving dependencies.
    """
    return await _readiness_response(response, db)


@router.get("/detailed", response_model=DetailedHealthCheck)
async def detailed_health_check(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> DetailedHealthCheck:
    """
    Detailed health check with component status.

    Checks:
    - Database connectivity
    - Redis connectivity
    - RabbitMQ connectivity
    - S3 bucket availability when S3 is configured

    Returns:
        Detailed health status with component information
    """
    return await _readiness_response(response, db)
