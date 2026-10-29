"""Guard against assigning platform roles to the system (sentinel) account.

Embedded-app and engine execution run as the system account
(``SYSTEM_USER_ID`` / ``SYSTEM_USER_UUID`` in ``src.core.constants``). Some
access checks resolve effective permissions by looking up a user's
``UserRole`` rows, so a role assigned to the system account would widen what
those sessions can do. Every writer of ``UserRole`` must reject the system
account as an assignment target.

REST role assignment and the bulk user-update endpoint each build ``UserRole``
rows directly. This module holds the shared check both writers call.
"""

from uuid import UUID

from src.core.constants import SYSTEM_USER_UUID

SYSTEM_ACCOUNT_ROLE_MESSAGE = "The system account can't be assigned roles."


def is_system_account(user_id: UUID) -> bool:
    """True if ``user_id`` is the platform system (sentinel) account."""
    return user_id == SYSTEM_USER_UUID
