"""
E2E User dataclass and helpers.

Provides the E2EUser dataclass for tracking user state through E2E tests,
including credentials, tokens, and organization membership.
"""

from dataclasses import dataclass
from typing import ClassVar
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

    # The shared E2E client only knows how to refresh credentials issued for a
    # fixture user. This intentionally leaves hand-crafted bearer tokens raw
    # for tests that assert an authentication failure.
    _users_by_access_token: ClassVar[dict[str, "E2EUser"]] = {}

    def set_tokens(self, access_token: str, refresh_token: str) -> None:
        """Store a rotated credential pair and make its access token refreshable."""
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._users_by_access_token[access_token] = self

    @classmethod
    def user_for_access_token(cls, access_token: str | None) -> "E2EUser | None":
        """Return the fixture user that owns a bearer token, if any."""
        return cls._users_by_access_token.get(access_token) if access_token else None

    @property
    def headers(self) -> dict[str, str]:
        """Auth headers for API requests."""
        if not self.access_token:
            raise ValueError(f"User {self.email} not authenticated - no access token")
        if self.refresh_token:
            self._users_by_access_token[self.access_token] = self
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
