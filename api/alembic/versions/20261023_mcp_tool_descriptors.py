"""Normalize external MCP tool schemas to canonical descriptors.

Revision ID: 20261023_mcp_tool_descriptors
Revises: 20261023_resource_requests
Create Date: 2026-10-23

Early external-MCP catalog sync persisted the raw ``inputSchema`` directly.
The canonical catalog shape stores that schema under ``inputSchema`` and keeps
the external tool's optional ``description`` and dict ``outputSchema`` as
separate descriptor metadata. Existing canonical descriptors stay untouched.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20261023_mcp_tool_descriptors"
down_revision: str | None = "20261023_resource_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def normalize_mcp_tool_schema_descriptors(connection: Connection) -> None:
    """Wrap only known legacy shapes without guessing ambiguous schema keys."""
    connection.execute(
        sa.text(
            """
            UPDATE mcp_connection_tools
            SET tool_schema = CASE
                WHEN tool_schema ? 'input_schema' THEN
                    jsonb_set(
                        tool_schema - 'input_schema',
                        '{inputSchema}',
                        tool_schema -> 'input_schema',
                        TRUE
                    )
                ELSE
                    jsonb_build_object(
                        'inputSchema', CASE
                            WHEN jsonb_typeof(tool_schema -> 'outputSchema') = 'object'
                            THEN tool_schema - 'outputSchema'
                            ELSE tool_schema
                        END
                    ) || CASE
                        WHEN jsonb_typeof(tool_schema -> 'outputSchema') = 'object'
                        THEN jsonb_build_object(
                            'outputSchema', tool_schema -> 'outputSchema'
                        )
                        ELSE '{}'::jsonb
                    END || CASE
                        WHEN jsonb_typeof(tool_schema -> 'description') = 'string'
                        THEN jsonb_build_object('description', tool_schema -> 'description')
                        ELSE '{}'::jsonb
                    END
            END
            WHERE jsonb_typeof(tool_schema) = 'object'
              AND NOT tool_schema ? 'inputSchema'
              AND (
                  tool_schema ? 'input_schema'
                  OR jsonb_typeof(tool_schema -> 'type') = 'string'
              )
            """
        )
    )


def upgrade() -> None:
    normalize_mcp_tool_schema_descriptors(op.get_bind())


def downgrade() -> None:
    # The former flattened representation cannot distinguish input-schema
    # annotations from tool descriptor metadata, so it has no lossless reverse.
    pass
