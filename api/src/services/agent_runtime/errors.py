"""Control-flow exceptions shared by Bifrost agent surfaces."""


class AgentRunCancelled(Exception):
    """Stop a Pydantic AI run because Bifrost received a cancellation request."""
