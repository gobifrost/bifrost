"""DB-level guarantee: ``user_roles`` cannot reference the system account.

Belt-and-suspenders alongside the application-level guard in
``shared.system_account_guard`` — a CHECK constraint added in
``20260927_user_roles_no_system`` makes a direct/raw insert impossible too,
not just the audited application code paths.
"""

import pytest
import sqlalchemy.exc

from src.core.constants import SYSTEM_USER_UUID
from src.models import Role, UserRole


@pytest.mark.e2e
async def test_direct_insert_for_system_account_is_rejected(db_session) -> None:
    db = db_session
    role = Role(
        name=f"constraint-test-{SYSTEM_USER_UUID.hex[:8]}",
        description=None,
        created_by="test",
    )
    db.add(role)
    await db.flush()

    db.add(
        UserRole(
            user_id=SYSTEM_USER_UUID,
            role_id=role.id,
            assigned_by="test",
        )
    )
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await db.flush()
