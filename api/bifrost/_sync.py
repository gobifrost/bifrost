"""
Sync module for flushing pending changes to Postgres.

After workflow execution completes, this module reads buffered changes
from Redis and applies them to Postgres in a single transaction.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import delete, select

from src.core.cache import get_shared_redis, pending_changes_key

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SyncError(Exception):
    """Failed to sync pending changes to Postgres."""
    pass


async def flush_pending_changes(
    execution_id: str,
    session: "AsyncSession | None" = None,
) -> int:
    """
    Flush all pending changes for an execution to Postgres.

    Args:
        execution_id: Execution ID to flush
        session: Optional database session. If provided, uses it and
                 caller is responsible for commit. If None, creates own session.

    Returns:
        int: Number of changes applied

    Raises:
        SyncError: If flush fails after retries
    """
    r = await get_shared_redis()
    redis_key = pending_changes_key(execution_id)

    pending = await r.hgetall(redis_key)  # type: ignore[misc]
    if not pending:
        return 0

    changes: list[dict[str, Any]] = []
    for value in pending.values():
        try:
            changes.append(json.loads(value))
        except json.JSONDecodeError:
            continue

    changes.sort(key=lambda c: c.get("sequence", 0))

    async def _apply_all(db: "AsyncSession") -> None:
        for change in changes:
            await _apply_change(db, change)

    for attempt in range(3):
        try:
            if session is not None:
                # Use provided session (caller manages commit)
                await _apply_all(session)
            else:
                # Create own session
                from src.core.database import get_session_factory

                session_factory = get_session_factory()
                async with session_factory() as db:
                    await _apply_all(db)
                    await db.commit()

            await r.delete(redis_key)
            logger.info(f"Flushed {len(changes)} changes for {execution_id}")
            return len(changes)
        except Exception as e:
            if attempt == 2:
                raise SyncError(f"Failed to flush: {e}") from e

    return 0


async def _apply_change(db: "AsyncSession", change: dict[str, Any]) -> None:
    """Apply a single change to Postgres."""
    entity_type = change.get("entity_type")

    if entity_type == "config":
        await _apply_config_change(db, change)
    elif entity_type == "role":
        await _apply_role_change(db, change)
    elif entity_type == "organization":
        await _apply_org_change(db, change)
    elif entity_type == "user_role":
        await _apply_user_role_change(db, change)
    elif entity_type == "form_role":
        await _apply_form_role_change(db, change)


async def _apply_config_change(db: "AsyncSession", change: dict[str, Any]) -> None:
    """Apply config change."""
    from src.models.enums import ConfigType
    from src.models import Config

    key = change.get("entity_key")
    org_id = change.get("org_id")
    data = change.get("data", {})
    user_id = change.get("user_id")
    operation = change.get("operation")

    org_uuid = UUID(org_id) if org_id and org_id != "GLOBAL" else None

    if operation == "delete":
        await db.execute(
            delete(Config).where(
                Config.key == key,
                Config.organization_id == org_uuid if org_uuid else Config.organization_id.is_(None)
            )
        )
    else:
        if org_uuid:
            result = await db.execute(select(Config).where(Config.key == key, Config.organization_id == org_uuid))
        else:
            result = await db.execute(select(Config).where(Config.key == key, Config.organization_id.is_(None)))
        existing = result.scalars().first()

        config_type = ConfigType(data.get("config_type", "string"))

        if existing:
            existing.value = {"value": data.get("value")}
            existing.config_type = config_type
            existing.updated_by = user_id or "system"
        else:
            db.add(Config(
                organization_id=org_uuid,
                key=key,
                value={"value": data.get("value")},
                config_type=config_type,
                updated_by=user_id or "system"
            ))


async def _apply_role_change(db: "AsyncSession", change: dict[str, Any]) -> None:
    """Apply role change."""
    from src.models import Role

    operation = change.get("operation")
    role_id = change.get("entity_id")
    entity_key = change.get("entity_key")
    org_id = change.get("org_id")
    data = change.get("data", {})
    user_id = change.get("user_id") or "system"

    org_uuid = UUID(org_id) if org_id and org_id != "GLOBAL" else None
    role_uuid = UUID(role_id) if role_id else UUID(entity_key)

    if operation == "delete":
        result = await db.execute(select(Role).where(Role.id == role_uuid))
        role = result.scalars().first()
        if role:
            await db.delete(role)
    elif operation == "update":
        result = await db.execute(select(Role).where(Role.id == role_uuid))
        role = result.scalars().first()
        if role:
            for field in ["name", "description"]:
                if field in data:
                    setattr(role, field, data[field])
    elif operation == "create":
        db.add(Role(
            id=role_uuid,
            organization_id=org_uuid,
            name=data.get("name", ""),
            description=data.get("description", ""),
            is_active=True,
            created_by=user_id
        ))


async def _apply_org_change(db: "AsyncSession", change: dict[str, Any]) -> None:
    """Apply organization change."""
    from src.models import Organization

    operation = change.get("operation")
    org_id = change.get("entity_id")
    entity_key = change.get("entity_key")
    data = change.get("data", {})
    user_id = change.get("user_id") or "system"

    org_uuid = UUID(org_id) if org_id else UUID(entity_key)

    if operation == "delete":
        result = await db.execute(select(Organization).where(Organization.id == org_uuid))
        org = result.scalars().first()
        if org:
            org.is_active = False
    elif operation == "update":
        result = await db.execute(select(Organization).where(Organization.id == org_uuid))
        org = result.scalars().first()
        if org:
            for field in ["name", "domain", "is_active"]:
                if field in data:
                    setattr(org, field, data[field])
    elif operation == "create":
        db.add(Organization(
            id=org_uuid,
            name=data.get("name", ""),
            domain=data.get("domain"),
            is_active=data.get("is_active", True),
            created_by=user_id
        ))


async def _apply_user_role_change(db: "AsyncSession", change: dict[str, Any]) -> None:
    """Apply user-role assignment."""
    from sqlalchemy import and_
    from src.models import UserRole
    from shared.system_account_guard import (
        SYSTEM_ACCOUNT_ROLE_MESSAGE,
        is_system_account,
    )

    role_id = change.get("entity_id")
    data = change.get("data", {})
    assigned_by = change.get("user_id") or "system"
    role_uuid = UUID(role_id)

    for user_id in data.get("user_ids", []):
        user_uuid = UUID(user_id)
        if is_system_account(user_uuid):
            raise SyncError(SYSTEM_ACCOUNT_ROLE_MESSAGE)
        result = await db.execute(
            select(UserRole).where(and_(UserRole.role_id == role_uuid, UserRole.user_id == user_uuid))
        )
        if not result.scalars().first():
            db.add(UserRole(role_id=role_uuid, user_id=user_uuid, assigned_by=assigned_by))


async def _apply_form_role_change(db: "AsyncSession", change: dict[str, Any]) -> None:
    """Apply form-role assignment."""
    from sqlalchemy import and_
    from src.models import FormRole

    role_id = change.get("entity_id")
    data = change.get("data", {})
    assigned_by = change.get("user_id") or "system"
    role_uuid = UUID(role_id)

    for form_id in data.get("form_ids", []):
        form_uuid = UUID(form_id)
        result = await db.execute(
            select(FormRole).where(and_(FormRole.role_id == role_uuid, FormRole.form_id == form_uuid))
        )
        if not result.scalars().first():
            db.add(FormRole(role_id=role_uuid, form_id=form_uuid, assigned_by=assigned_by))
