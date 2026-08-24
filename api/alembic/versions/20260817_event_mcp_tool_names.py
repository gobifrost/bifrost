"""rename persisted Event MCP tool identifiers

Revision ID: 20260817_event_mcp_names
Revises: 20260817_app_mcp_names

Event Source, Event Subscription, and webhook-adapter identifiers stored in
Agent ``system_tools`` move atomically with their canonical MCP registrations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_event_mcp_names"
down_revision: str = "20260817_app_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_event_sources", "bifrost_list_event_sources")
    _replace("get_event_source", "bifrost_get_event_source")
    _replace("create_event_source", "bifrost_create_event_source")
    _replace("update_event_source", "bifrost_update_event_source")
    _replace("delete_event_source", "bifrost_delete_event_source")
    _replace("list_event_subscriptions", "bifrost_list_event_subscriptions")
    _replace("create_event_subscription", "bifrost_create_event_subscription")
    _replace("update_event_subscription", "bifrost_update_event_subscription")
    _replace("delete_event_subscription", "bifrost_delete_event_subscription")
    _replace(
        "list_webhook_adapters",
        "bifrost_list_event_webhook_adapters",
    )


def downgrade() -> None:
    _replace("bifrost_list_event_sources", "list_event_sources")
    _replace("bifrost_get_event_source", "get_event_source")
    _replace("bifrost_create_event_source", "create_event_source")
    _replace("bifrost_update_event_source", "update_event_source")
    _replace("bifrost_delete_event_source", "delete_event_source")
    _replace("bifrost_list_event_subscriptions", "list_event_subscriptions")
    _replace("bifrost_create_event_subscription", "create_event_subscription")
    _replace("bifrost_update_event_subscription", "update_event_subscription")
    _replace("bifrost_delete_event_subscription", "delete_event_subscription")
    _replace(
        "bifrost_list_event_webhook_adapters",
        "list_webhook_adapters",
    )
