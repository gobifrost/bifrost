"""Kitchen-sink lookup tool used by the triage agent."""


def sink_lookup(query: str) -> dict:
    """Look up one record by query string."""
    return {"query": query, "hits": []}
