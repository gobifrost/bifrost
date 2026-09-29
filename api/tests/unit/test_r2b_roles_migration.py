"""Schema-level coverage for `alembic/versions/20260929_r2b_roles.py` that the
true migration rehearsal (`tests/e2e/platform/test_r2b_roles_migration_rehearsal.py`)
doesn't exercise: a DB-level CHECK constraint, which isn't observable from
data assertions alone.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from shared.builtin_roles import USER_ROLE_ID


@pytest.mark.asyncio
async def test_user_role_boundaries_table_shape(db_session):
    """The boundaries table exists with the documented kind vocabulary
    enforced at the DB level (a bad kind is rejected)."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id) "
                    "SELECT gen_random_uuid(), u.id, :rid, 'not_a_real_kind', NULL FROM users u LIMIT 1"
                ),
                {"rid": str(USER_ROLE_ID)},
            )
