"""Public error copy for the Chat user experience."""

CHAT_FAILURE_MESSAGE = "We couldn't complete this response. Please try again."
CHAT_TIMEOUT_MESSAGE = "This response took too long to complete. Please try again."


def public_chat_error_message(status: str | None) -> str:
    """Return safe public copy for a terminal Chat failure."""
    if status == "timeout":
        return CHAT_TIMEOUT_MESSAGE
    return CHAT_FAILURE_MESSAGE
