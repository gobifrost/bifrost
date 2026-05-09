import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def get_version() -> str:
    if v := os.environ.get("BIFROST_VERSION"):
        return v
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"
