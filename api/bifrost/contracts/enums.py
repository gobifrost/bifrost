"""CLI-side mirror of the server enums used to build flag surfaces.

Only the enums referenced (directly or via DTO fields) by CLI commands
are mirrored. Value parity with ``src.models.enums`` is enforced by
``api/tests/unit/test_contracts_parity.py`` — adding a new member to the
server enum will fail that test until the mirror is updated.
"""

from __future__ import annotations

from enum import Enum


class FormAccessLevel(str, Enum):
    """Form access control levels.

    AUTHENTICATED is "Everyone except external users" in the UI; EVERYONE
    additionally grants to external (portal/guest) users.
    """

    AUTHENTICATED = "authenticated"
    EVERYONE = "everyone"
    ROLE_BASED = "role_based"


class AgentAccessLevel(str, Enum):
    """Agent access control levels.

    AUTHENTICATED is "Everyone except external users" in the UI; EVERYONE
    additionally grants to external (portal/guest) users.
    """

    AUTHENTICATED = "authenticated"
    EVERYONE = "everyone"
    ROLE_BASED = "role_based"
    PRIVATE = "private"


class AgentChannel(str, Enum):
    """Supported agent communication channels."""

    CHAT = "chat"
    VOICE = "voice"
    TEAMS = "teams"
    SLACK = "slack"


class ConfigType(str, Enum):
    """Configuration value types."""

    STRING = "string"
    INT = "int"
    BOOL = "bool"
    JSON = "json"
    SECRET = "secret"


class EventSourceType(str, Enum):
    """Event source types."""

    WEBHOOK = "webhook"
    SCHEDULE = "schedule"
    TOPIC = "topic"
