"""
Entity Change Hook — broadcasts entity mutations via file-activity WebSocket channel.

Registers SQLAlchemy ORM event listeners that detect inserts/updates/deletes on
manifest-relevant models. After commit, publishes ``entity_change`` events so
CLI watch sessions can update their local ``.bifrost/*.yaml`` files in real time.

The hook:
1. ``after_flush``: inspects ``session.new``, ``session.dirty``, ``session.deleted``
   for manifest-relevant models and records pending changes.
2. ``after_commit``: fires async tasks to serialize and broadcast each change.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ORM model class → (manifest_entity_type, id_attr)
# Maps each watched model to the manifest section it belongs to.
_MODEL_REGISTRY: dict[type, tuple[str, str]] = {}


def _register_models() -> None:
    """Populate _MODEL_REGISTRY with manifest-relevant ORM models."""
    if _MODEL_REGISTRY:
        return

    from src.models.orm.workflows import Workflow
    from src.models.orm.forms import Form
    from src.models.orm.agents import Agent
    from src.models.orm.applications import Application
    from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
    from src.models.orm.config import Config
    from src.models.orm.tables import Table
    from src.models.orm.events import EventSource, EventSubscription
    from src.models.orm.organizations import Organization
    from src.models.orm.users import Role
    from src.models.orm.oauth import OAuthProvider

    _MODEL_REGISTRY.update({
        Workflow: ("workflows", "id"),
        Form: ("forms", "id"),
        Agent: ("agents", "id"),
        Application: ("apps", "id"),
        Integration: ("integrations", "id"),
        IntegrationConfigSchema: ("integrations", "integration_id"),
        IntegrationMapping: ("integrations", "integration_id"),
        Config: ("configs", "id"),
        Table: ("tables", "id"),
        EventSource: ("events", "id"),
        EventSubscription: ("events", "event_source_id"),
        Organization: ("organizations", "id"),
        Role: ("roles", "id"),
        OAuthProvider: ("integrations", "integration_id"),
    })


# Attribute name used to stash pending changes on the session object
_PENDING_ATTR = "_bifrost_entity_changes"


def _get_pending(session: Session) -> list[tuple[str, str, str]]:
    """Get or create the pending changes list on a session."""
    pending = getattr(session, _PENDING_ATTR, None)
    if pending is None:
        pending = []
        setattr(session, _PENDING_ATTR, pending)
    return pending


def _extract_entity_key(instance: Any) -> tuple[str, str, str] | None:
    """Extract (entity_type, entity_id, action) from an ORM instance.

    Returns None if the instance's model class is not manifest-relevant.
    """
    cls = type(instance)
    entry = _MODEL_REGISTRY.get(cls)
    if entry is None:
        return None
    entity_type, id_attr = entry
    entity_id = str(getattr(instance, id_attr, ""))
    if not entity_id:
        return None
    return (entity_type, entity_id, "")  # action filled by caller


def _after_flush(session: Session, flush_context: Any) -> None:
    """SQLAlchemy after_flush event — collect manifest-relevant changes."""
    pending = _get_pending(session)

    for instance in session.new:
        key = _extract_entity_key(instance)
        if key:
            pending.append((key[0], key[1], "add"))

    for instance in session.dirty:
        key = _extract_entity_key(instance)
        if key:
            pending.append((key[0], key[1], "update"))

    for instance in session.deleted:
        key = _extract_entity_key(instance)
        if key:
            pending.append((key[0], key[1], "delete"))


def _after_commit(session: Session) -> None:
    """SQLAlchemy after_commit event — schedule async broadcasts for pending changes."""
    pending: list[tuple[str, str, str]] = getattr(session, _PENDING_ATTR, [])
    if not pending:
        return

    # Deduplicate: same (entity_type, entity_id) may appear multiple times,
    # keep the last action (add + update → update, update + delete → delete).
    seen: dict[tuple[str, str], str] = {}
    for entity_type, entity_id, action in pending:
        seen[(entity_type, entity_id)] = action

    # Clear pending
    setattr(session, _PENDING_ATTR, [])

    # Get user/session context
    from src.core.request_context import get_request_user, get_request_session_id
    req_user = get_request_user()
    user_id = req_user.user_id if req_user else "system"
    user_name = req_user.user_name if req_user else "system"
    session_id = get_request_session_id()

    # Schedule async publishes
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # No event loop (e.g. test or CLI context)

    for (entity_type, entity_id), action in seen.items():
        loop.create_task(_publish_entity_change(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            user_id=user_id,
            user_name=user_name,
            session_id=session_id,
        ))


async def _serialize_entity(entity_type: str, entity_id: str) -> dict[str, Any] | None:
    """Query and serialize an entity for inclusion in change events.

    Opens a fresh DB session, loads the entity + related data, and returns
    the manifest-serialized dict.  Returns None on any failure.

    Note: this deliberately calls ``serialize_X(...).model_dump(mode="json",
    exclude_defaults=True, by_alias=True)`` rather than the unified
    ``.view(Destination.GIT_SYNC)`` seam. Change events want a compact diff
    payload (``exclude_defaults`` drops unchanged-from-default fields), which
    ``view()`` does not apply — so this is one wire surface intentionally not
    routed through ``view()``. Keep them aligned if the GIT_SYNC view ever
    gains an exclude-defaults variant.
    """
    from src.core.database import get_db_context
    from sqlalchemy import select

    try:
        async with get_db_context() as db:
            if entity_type == "workflows":
                from src.models.orm.workflows import Workflow
                from src.models.orm.workflow_roles import WorkflowRole
                from src.services.manifest_generator import serialize_workflow

                row = (await db.execute(select(Workflow).where(Workflow.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                wr = (await db.execute(select(WorkflowRole).where(WorkflowRole.workflow_id == entity_id))).scalars().all()
                roles = [str(r.role_id) for r in wr]
                return serialize_workflow(row, roles).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "forms":
                from src.models.orm.forms import Form, FormRole
                from src.services.manifest_generator import serialize_form

                row = (await db.execute(select(Form).where(Form.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                fr = (await db.execute(select(FormRole).where(FormRole.form_id == entity_id))).scalars().all()
                roles = [str(r.role_id) for r in fr]
                return serialize_form(row, roles).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "agents":
                from src.models.orm.agents import Agent, AgentRole
                from src.services.manifest_generator import serialize_agent

                row = (await db.execute(select(Agent).where(Agent.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                ar = (await db.execute(select(AgentRole).where(AgentRole.agent_id == entity_id))).scalars().all()
                roles = [str(r.role_id) for r in ar]
                return serialize_agent(row, roles).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "apps":
                from src.models.orm.applications import Application
                from src.models.orm.app_roles import AppRole
                from src.services.manifest_generator import serialize_app

                row = (await db.execute(select(Application).where(Application.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                ar = (await db.execute(select(AppRole).where(AppRole.app_id == entity_id))).scalars().all()
                roles = [str(r.role_id) for r in ar]
                return serialize_app(row, roles).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "integrations":
                from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
                from src.models.orm.oauth import OAuthProvider
                from src.services.manifest_generator import serialize_integration

                row = (await db.execute(select(Integration).where(Integration.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                cs = (await db.execute(select(IntegrationConfigSchema).where(IntegrationConfigSchema.integration_id == entity_id))).scalars().all()
                oauth = (await db.execute(select(OAuthProvider).where(OAuthProvider.integration_id == entity_id))).scalar_one_or_none()
                mappings = (await db.execute(select(IntegrationMapping).where(IntegrationMapping.integration_id == entity_id))).scalars().all()
                return serialize_integration(row, list(cs), oauth, list(mappings)).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "configs":
                from src.models.orm.config import Config
                from src.services.manifest_generator import serialize_config

                row = (await db.execute(select(Config).where(Config.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                return serialize_config(row).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "tables":
                from src.models.orm.tables import Table
                from src.services.manifest_generator import serialize_table

                row = (await db.execute(select(Table).where(Table.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                return serialize_table(row).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "events":
                from src.models.orm.events import EventSource, EventSubscription, ScheduleSource, WebhookSource
                from src.services.manifest_generator import serialize_event_source

                row = (await db.execute(select(EventSource).where(EventSource.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                sched = (await db.execute(select(ScheduleSource).where(ScheduleSource.event_source_id == entity_id))).scalar_one_or_none()
                wh = (await db.execute(select(WebhookSource).where(WebhookSource.event_source_id == entity_id))).scalar_one_or_none()
                subs = (await db.execute(select(EventSubscription).where(EventSubscription.event_source_id == entity_id))).scalars().all()
                return serialize_event_source(row, sched, wh, list(subs)).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "organizations":
                from src.models.orm.organizations import Organization
                from src.services.manifest_generator import serialize_organization

                row = (await db.execute(select(Organization).where(Organization.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                return serialize_organization(row).model_dump(mode="json", exclude_defaults=True, by_alias=True)

            elif entity_type == "roles":
                from src.models.orm.users import Role
                from src.services.manifest_generator import serialize_role

                row = (await db.execute(select(Role).where(Role.id == entity_id))).scalar_one_or_none()
                if not row:
                    return None
                return serialize_role(row).model_dump(mode="json", exclude_defaults=True, by_alias=True)

    except Exception as e:
        logger.warning(f"Failed to serialize {entity_type}/{entity_id}: {e}")
    return None


async def _publish_entity_change(
    entity_type: str,
    entity_id: str,
    action: str,
    user_id: str,
    user_name: str,
    session_id: str | None,
) -> None:
    """Publish a single entity_change event via file-activity channel."""
    try:
        from src.core.pubsub import publish_file_activity

        # For non-delete actions, serialize the entity to include in the event
        data: dict[str, Any] | None = None
        if action != "delete":
            data = await _serialize_entity(entity_type, entity_id)

        await publish_file_activity(
            user_id=user_id,
            user_name=user_name,
            activity_type="entity_change",
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            session_id=session_id,
            data=data,
        )
    except Exception as e:
        logger.warning(f"Failed to publish entity_change for {entity_type}/{entity_id}: {e}")


def _after_rollback(session: Session) -> None:
    """Clear pending changes on rollback."""
    setattr(session, _PENDING_ATTR, [])


def register_entity_change_hooks() -> None:
    """Register SQLAlchemy event listeners for entity change tracking.

    Call this once during application startup (after engine/session factory init).
    """
    _register_models()

    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "after_rollback", _after_rollback)

    logger.info(
        f"Entity change hooks registered for {len(_MODEL_REGISTRY)} model types"
    )
