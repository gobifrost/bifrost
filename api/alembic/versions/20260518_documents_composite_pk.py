"""Promote documents PK to composite (table_id, id).

Revision ID: 20260518_documents_composite_pk
Revises: 20260506_knowledge_dim
Create Date: 2026-05-18

The original schema (20260101) put the primary key on ``id`` alone, then
20260102_010000 added a composite unique index ``ix_documents_table_id_id_unique``
on ``(table_id, id)``. The narrower PK meant document ids were globally
unique across all tables, which broke any workflow using a shared
doc-id convention (e.g. ``cache-<org_id>`` across multiple cache tables):

- The second table's write hit ``ON CONFLICT (table_id, id) DO UPDATE``,
  which only handles the composite unique index — not the PK.
- The INSERT then violated ``documents_pkey`` on ``id`` alone, raising
  IntegrityError → global handler → 409 "Resource already exists".
- Reads filter by ``(table_id, id)`` so the colliding row was invisible
  to the calling table — appearing as a 404 read / 409 write "zombie".

The fix promotes the PK to ``(table_id, id)``. The composite unique index
becomes redundant and is dropped.

Since the narrower PK already enforced global uniqueness of ``id``, no
data prep is needed — every existing row satisfies the wider composite
PK by construction.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260518_documents_composite_pk"
down_revision = "20260506_knowledge_dim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The composite unique index is redundant once (table_id, id) is the PK.
    op.drop_index("ix_documents_table_id_id_unique", table_name="documents")
    op.drop_constraint("documents_pkey", "documents", type_="primary")
    op.create_primary_key("documents_pkey", "documents", ["table_id", "id"])


def downgrade() -> None:
    op.drop_constraint("documents_pkey", "documents", type_="primary")
    op.create_primary_key("documents_pkey", "documents", ["id"])
    op.create_index(
        "ix_documents_table_id_id_unique",
        "documents",
        ["table_id", "id"],
        unique=True,
    )
