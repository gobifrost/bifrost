"""Process-local aiobotocore session ownership."""

from __future__ import annotations

import os

from aiobotocore.session import AioSession, get_session


_session: tuple[int, AioSession] | None = None


def get_s3_session() -> AioSession:
    """Return this process's reusable aiobotocore session.

    Callers own client lifetimes; this factory retains no client transport
    resources. Recreate the session after a fork because aiobotocore state is
    process-local.
    """
    global _session

    pid = os.getpid()
    if _session is None or _session[0] != pid:
        _session = (pid, get_session())
    return _session[1]
