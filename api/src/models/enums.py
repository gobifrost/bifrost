"""
Enumeration types used across the application.

These match the existing enums in shared/models.py for compatibility.
"""

from enum import Enum


class ExecutionStatus(str, Enum):
    """Workflow execution status"""
    SCHEDULED = "Scheduled"
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCESS = "Success"
    FAILED = "Failed"
    TIMEOUT = "Timeout"
    STUCK = "Stuck"  # Execution did not respond to cancellation within grace period
    COMPLETED_WITH_ERRORS = "CompletedWithErrors"
    CANCELLING = "Cancelling"
    CANCELLED = "Cancelled"


class FormAccessLevel(str, Enum):
    """Form access control levels.

    AUTHENTICATED is "Everyone except external users" in the UI; EVERYONE
    additionally grants to external (portal/guest) users.
    """
    AUTHENTICATED = "authenticated"
    EVERYONE = "everyone"
    ROLE_BASED = "role_based"


class FormFieldType(str, Enum):
    """Form field types"""
    TEXT = "text"
    EMAIL = "email"
    NUMBER = "number"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    CHECKBOX = "checkbox"
    TEXTAREA = "textarea"
    RADIO = "radio"
    DATE = "date"
    DATETIME = "datetime"
    MARKDOWN = "markdown"
    HTML = "html"
    FILE = "file"


class ConfigType(str, Enum):
    """Configuration value types"""
    STRING = "string"
    INT = "int"
    BOOL = "bool"
    JSON = "json"
    SECRET = "secret"  # Value is encrypted


class MFAMethodType(str, Enum):
    """Supported MFA method types"""
    TOTP = "totp"
    SMS = "sms"
    EMAIL = "email"
    WEBAUTHN = "webauthn"


class MFAMethodStatus(str, Enum):
    """MFA method enrollment status"""
    PENDING = "pending"
    ACTIVE = "active"
    DISABLED = "disabled"


class GitStatus(str, Enum):
    """Git sync status for workspace files"""
    UNTRACKED = "untracked"
    SYNCED = "synced"
    MODIFIED = "modified"
    DELETED = "deleted"


class AgentAccessLevel(str, Enum):
    """Agent access control levels.

    AUTHENTICATED is "Everyone except external users" in the UI; EVERYONE
    additionally grants to external (portal/guest) users.
    """
    AUTHENTICATED = "authenticated"
    EVERYONE = "everyone"
    ROLE_BASED = "role_based"
    PRIVATE = "private"


class AppAccessLevel(str, Enum):
    """Application access control levels.

    AUTHENTICATED is "Everyone except external users" in the UI; EVERYONE
    additionally grants to external (portal/guest) users.
    """
    AUTHENTICATED = "authenticated"
    EVERYONE = "everyone"
    ROLE_BASED = "role_based"


class MessageRole(str, Enum):
    """Message roles in chat conversations"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    TOOL_CALL = "tool_call"


class AgentChannel(str, Enum):
    """Supported agent communication channels"""
    CHAT = "chat"
    VOICE = "voice"
    TEAMS = "teams"
    SLACK = "slack"


class EventSourceType(str, Enum):
    """Event source types"""
    WEBHOOK = "webhook"
    SCHEDULE = "schedule"
    TOPIC = "topic"


class EventStatus(str, Enum):
    """Event processing status"""
    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class EventDeliveryStatus(str, Enum):
    """Event delivery status to a workflow"""
    PENDING = "pending"
    QUEUED = "queued"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExecutionModel(str, Enum):
    """
    Execution model used for workflow execution.

    Tracks which worker model was used:
    - PROCESS: Process pool model (process_pool.py + simple_worker.py)
    """
    PROCESS = "process"


class ScheduleOverlapPolicy(str, Enum):
    """Behavior when a schedule fires while a previous run is still active."""
    SKIP = "skip"
    QUEUE = "queue"
    REPLACE = "replace"
