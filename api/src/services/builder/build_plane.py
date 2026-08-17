"""Shared build contract constants and readiness error."""

# Bump when the published sandbox runner toolchain changes so artifacts built
# by an older image cannot be silently reused by a newer release.
TOOLCHAIN_VERSION = "node20-vite5-v1"


class BuildPlaneUnavailable(Exception):
    """The configured sandbox provider is not ready to accept builds."""
