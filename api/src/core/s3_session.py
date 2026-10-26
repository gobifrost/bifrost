"""Process-local aiobotocore session ownership."""

from __future__ import annotations

import os

from aiobotocore.session import AioSession, get_session


_session: AioSession | None = None
_session_pid: int | None = None


def get_s3_session() -> AioSession:
    """Return this process's reusable aiobotocore session.

    Callers own client lifetimes; this factory retains no client transport
    resources. Recreate the session after a fork because aiobotocore state is
    process-local.
    """
    global _session, _session_pid

    pid = os.getpid()
    if _session is None or _session_pid != pid:
        _session = get_session()
        _session_pid = pid
    return _session
