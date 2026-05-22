"""
E2E User dataclass and helpers.

Provides the E2EUser dataclass for tracking user state through E2E tests,
including credentials, tokens, and organization membership.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass
class E2EUser:
    """Tracks user state through E2E test flow."""

    email: str
    password: str
    name: str
    user_id: UUID | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    totp_secret: str | None = None
    organization_id: UUID | None = None
    is_superuser: bool = False

    @property
    def headers(self) -> dict[str, str]:
        """Auth headers for API requests."""
        if not self.access_token:
            raise ValueError(f"User {self.email} not authenticated - no access token")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
