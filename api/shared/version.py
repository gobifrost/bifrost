import os
import subprocess
from functools import lru_cache


# Old CLIs below this release do not implement the portable Solution targeting
# contract. The API exposes this floor at /api/version and compatible CLIs hard-
# block command dispatch until they are upgraded.
MIN_CLI_VERSION = "1.2.2"


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
