"""Only engine.execute's public_url setting; no server settings/DB/Redis."""
from types import SimpleNamespace


def get_settings():
    return SimpleNamespace(public_url="http://localhost")
