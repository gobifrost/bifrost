"""PID-owned factory for the narrow CRT-backed S3 transport."""

from __future__ import annotations

import os
from typing import Any

from src.core.s3_transport import S3Transport


class S3TransportFactory:
    """Create one HTTPX client per caller-owned async context."""

    def create_client(self, service_name: str, **kwargs: Any) -> S3Transport:
        if service_name != "s3":
            raise ValueError(f"unsupported service: {service_name}")
        return S3Transport(
            endpoint_url=kwargs.pop("endpoint_url", None),
            access_key_id=kwargs.pop("aws_access_key_id", None),
            secret_access_key=kwargs.pop("aws_secret_access_key", None),
            session_token=kwargs.pop("aws_session_token", None),
            region=kwargs.pop("region_name", None),
            **kwargs,
        )


_session: tuple[int, S3TransportFactory] | None = None


def get_s3_session() -> S3TransportFactory:
    """Return this process's transport factory, recreated after a fork."""
    global _session

    pid = os.getpid()
    if _session is None or _session[0] != pid:
        _session = (pid, S3TransportFactory())
    return _session[1]
