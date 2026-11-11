"""Provider-agnostic Hyperdrive PostgreSQL boundary.

Hyperdrive is a Workers binding for an adjacent read-only PostgreSQL probe.
It never replaces Bifrost's canonical direct PostgreSQL connections, which
remain authoritative for demand, admission, claims, progress, cancellation,
recovery, and scheduler leases.

This module names no cloud provider, hostname, account, network, or secret.
Azure Database for PostgreSQL, Neon, Amazon RDS, AlloyDB, and self-hosted
PostgreSQL are all acceptable origins when each deployment supplies its own
qualified private route, dedicated test database/role, and TLS policy.
"""

from __future__ import annotations

import re

# Advisory locks and LISTEN/NOTIFY are unsupported through Hyperdrive.
# Canonical coordination must stay on direct PostgreSQL.
HYPERDRIVE_UNSUPPORTED_POSTGRES_FEATURES: frozenset[str] = frozenset(
    {
        "pg_advisory_lock",
        "pg_try_advisory_lock",
        "LISTEN",
        "NOTIFY",
        "pg_notify",
    }
)

PROBE_READONLY_QUERY = (
    "SELECT 1 AS ok, "
    "current_setting('transaction_read_only') AS read_only, "
    "current_database() AS database, "
    "current_user AS role, "
    "session_user AS session_role"
)

_TARGET_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,62}$")


def validate_probe_target(*, database: str, role: str) -> None:
    """Validate a Hyperdrive probe target without echoing secrets.

    Both names must be explicit deployment inputs. Hostnames, URLs,
    credentials, and provider-specific suffixes do not belong here.
    """
    for label, value in (("database", database), ("role", role)):
        if not isinstance(value, str) or not _TARGET_NAME_RE.match(value):
            raise ValueError(f"Explicit valid {label} is required")


def is_direct_postgres_url(url: str) -> bool:
    """Return True when a URL is a direct PostgreSQL connection string.

    Canonical Bifrost pools require direct PostgreSQL. Hyperdrive connection
    strings are only available inside the Workers runtime via its binding
    and are never used to configure Python engines.
    """
    if not isinstance(url, str):
        return False
    lowered = url.strip().lower()
    return lowered.startswith(
        ("postgresql://", "postgresql+asyncpg://", "postgresql+psycopg://")
    )
