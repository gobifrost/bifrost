"""
Environment for subprocesses that run third-party package tooling.

``pip install`` executes package build hooks and ``npm install`` / ``vite build``
execute package scripts and plugins. Those processes get an allowlisted copy of
the environment — locale, paths, proxy and CA settings, and the tools' own
configuration — and never the platform's credentials (database, object
storage, Redis, RabbitMQ, signing secrets, API keys).
"""

from __future__ import annotations

import os
import re

_ALLOWED_NAMES = frozenset({
    "PATH",
    "HOME",
    "LANG",
    "TMPDIR",
    "TZ",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "VIRTUAL_ENV",
})

_ALLOWED_PREFIXES = ("LC_", "PIP_", "npm_config_", "NPM_CONFIG_", "NODE_", "PYTHON")

# Checked after the allowlist, so a tool-config name that carries a credential
# (for example ``NPM_CONFIG__AUTHTOKEN``) is still withheld.
_DENIED = re.compile(
    r"^(BIFROST_|DATABASE|AWS_|S3_|REDIS|RABBITMQ)|SECRET|TOKEN|PASSWORD",
    re.IGNORECASE,
)


def _allowed(name: str) -> bool:
    if _DENIED.search(name):
        return False
    return name in _ALLOWED_NAMES or name.startswith(_ALLOWED_PREFIXES)


def package_tool_env() -> dict[str, str]:
    """Return the environment for a pip / npm / npx subprocess."""
    return {name: value for name, value in os.environ.items() if _allowed(name)}
