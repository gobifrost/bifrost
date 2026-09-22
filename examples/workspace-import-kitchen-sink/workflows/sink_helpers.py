"""Shared helpers carried as an ordinary Python module (no workflow row)."""

SINK_VERSION = "1.0.0"


def normalize_name(value: str) -> str:
    """Normalize one display name."""
    return " ".join(value.split()).strip()
