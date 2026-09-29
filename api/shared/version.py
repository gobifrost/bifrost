import os
import subprocess
from functools import lru_cache


# Old CLIs below this (unreleased) floor cannot parse
# PlatformJobStatus.requires_action while polling durable jobs, and SDKs <=
# 1.4.1 require the removed WorkflowExecution.session_id field. SDKs <=
# 1.4.1 also still send/expect `permissions` on RoleCreate/RoleUpdate/
# RolePublic, which R2b removed (base roles + role_permissions replace it).
# 1.4.2 is the first release compatible with both changes. The API exposes
# this floor at /api/version and compatible CLIs hard-block command
# dispatch until they are upgraded.
MIN_CLI_VERSION = "1.4.2"


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
