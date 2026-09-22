"""Kitchen-sink alpha workflow (imported v2 definition)."""


def sink_alpha(payload: dict | None = None) -> dict:
    """Process one intake payload (v2)."""
    payload = payload or {}
    return {"status": "processed-v2", "echo": payload}
