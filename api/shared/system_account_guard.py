"""Guard against assigning platform roles to the system (sentinel) account.

Embedded-app and engine execution run as the system account
(``SYSTEM_USER_ID`` / ``SYSTEM_USER_UUID`` in ``src.core.constants``). Some
access checks resolve effective permissions by looking up a user's
``UserRole`` rows, so a role assigned to the system account would widen what
those sessions can do. Every writer of ``UserRole`` must reject the system
account as an assignment target.

There is no single shared repository all ``UserRole`` writers pass through
(REST role assignment, the bulk user-update endpoint, and the workflow
write-buffer flush each build the row directly), so this module holds the
one check and every writer calls it.
"""

from uuid import UUID

from src.core.constants import SYSTEM_USER_UUID

SYSTEM_ACCOUNT_ROLE_MESSAGE = "The system account can't be assigned roles."


def is_system_account(user_id: UUID) -> bool:
    """True if ``user_id`` is the platform system (sentinel) account."""
    return user_id == SYSTEM_USER_UUID
