"""
Redis key generation functions for Bifrost cache.

All Redis keys follow the pattern: bifrost:{scope}:{entity}:{id}
Where scope is either "global" or "org:{org_uuid}"

These functions are the SINGLE SOURCE OF TRUTH for key generation.
Used by both SDK (reads) and API routes (invalidation).
"""

from __future__ import annotations


def _get_scope(org_id: str | None) -> str:
    """Get the scope prefix for a key."""
    if org_id and org_id != "GLOBAL":
        return f"org:{org_id}"
    return "global"


# =============================================================================
# Config Keys
# =============================================================================


def config_hash_key(org_id: str | None) -> str:
    """
    Key for the hash containing all config values for an org.

    Structure: HASH where field = config key, value = JSON config data

    NOTE: Org-scoped config caches are merged views (global fallback
    overlaid with org-specific values). To invalidate every org cache
    efficiently on a global config write — without enumerating org
    keys via SCAN — the org-scoped key embeds a version suffix that
    advances when ``CONFIG_GLOBAL_VERSION_KEY`` is bumped.

    The lookup for the current version is async, so use
    ``config_hash_key_versioned`` instead from any caller that has
    a Redis client. This sync helper returns the BASE key (no version
    suffix) and is reserved for cases where the version is not needed
    (e.g. global-only reads, ``invalidate_all_config``).
    """
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:config"


def config_key(org_id: str | None, key: str) -> str:
    """
    Key for an individual config value (used for targeted invalidation).

    Note: We primarily use the hash (config_hash_key), but this is useful
    for invalidating specific keys.
    """
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:config:{key}"


CONFIG_GLOBAL_VERSION_KEY = "bifrost:config:global_version"


async def _current_global_version(redis_client) -> int:  # type: ignore[no-untyped-def]
    """Return the current global-config version, initializing to 0 if absent.

    The version is incremented on every global config write to invalidate
    every org's merged cache in O(1) without enumerating keys.
    """
    raw = await redis_client.get(CONFIG_GLOBAL_VERSION_KEY)
    if raw is None:
        return 0
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def config_hash_key_versioned(
    redis_client,  # type: ignore[no-untyped-def]
    org_id: str | None,
) -> str:
    """Versioned variant of ``config_hash_key``.

    Org-scoped keys embed the current global-version. Global keys are
    unversioned (the version IS the global-config-write signal).

    Reader and writer must use this helper so a global write naturally
    causes every org read to see a fresh cache key (and thus a DB
    re-merge on next read).
    """
    base = config_hash_key(org_id)
    if org_id is None or org_id == "GLOBAL":
        return base
    version = await _current_global_version(redis_client)
    return f"{base}:v{version}"


# =============================================================================
# Form Keys
# =============================================================================


def forms_hash_key(org_id: str | None) -> str:
    """
    Key for the hash containing all forms for an org.

    Structure: HASH where field = form_id, value = JSON form data
    """
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:forms"


def form_key(org_id: str | None, form_id: str) -> str:
    """Key for a specific form (for targeted invalidation)."""
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:forms:{form_id}"


def user_forms_key(org_id: str | None, user_id: str) -> str:
    """
    Key for the set of form IDs accessible by a specific user.

    Structure: SET of form UUIDs
    """
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:user_forms:{user_id}"


# =============================================================================
# Role Keys
# =============================================================================


def roles_hash_key(org_id: str | None) -> str:
    """
    Key for the hash containing all roles for an org.

    Structure: HASH where field = role_id, value = JSON role data
    """
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:roles"


def role_key(org_id: str | None, role_id: str) -> str:
    """Key for a specific role (for targeted invalidation)."""
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:roles:{role_id}"


def role_users_key(org_id: str | None, role_id: str) -> str:
    """
    Key for the set of user IDs assigned to a role.

    Structure: SET of user UUIDs
    """
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:roles:{role_id}:users"


def role_forms_key(org_id: str | None, role_id: str) -> str:
    """
    Key for the set of form IDs assigned to a role.

    Structure: SET of form UUIDs
    """
    scope = _get_scope(org_id)
    return f"bifrost:{scope}:roles:{role_id}:forms"


# =============================================================================
# Organization Keys
# =============================================================================


def org_key(org_id: str) -> str:
    """Key for a specific organization."""
    return f"bifrost:global:orgs:{org_id}"


def orgs_list_key() -> str:
    """Key for the list of all organizations."""
    return "bifrost:global:orgs:_list"


# =============================================================================
# Embed Execution Scoping Keys
# =============================================================================


def embed_execution_key(jti: str, execution_id: str) -> str:
    """Key linking an embed session (jti) to an execution it created."""
    return f"bifrost:embed:exec:{jti}:{execution_id}"


# =============================================================================
# Execution-Scoped Keys (Process Isolation)
# =============================================================================


def execution_pending_key(execution_id: str) -> str:
    """
    Key for pending execution data (written by API, read by worker).

    Structure: JSON serialized execution context including:
    - workflow_name, parameters, org_id, user_id, user_name, user_email
    - form_id, created_at, cancelled (bool)

    TTL: 1 hour (safety for orphaned entries)
    """
    return f"bifrost:exec:{execution_id}:pending"


def execution_context_key(execution_id: str) -> str:
    """
    Key for execution context (read by worker process).

    Structure: JSON serialized context data
    """
    return f"bifrost:exec:{execution_id}:context"


def execution_result_key(execution_id: str) -> str:
    """
    Key for execution result (written by worker process).

    Structure: JSON serialized result data
    """
    return f"bifrost:exec:{execution_id}:result"


def execution_cancel_key(execution_id: str) -> str:
    """
    Key for cancellation flag.

    Structure: "1" if cancelled
    """
    return f"bifrost:exec:{execution_id}:cancel"


# =============================================================================
# Execution-Scoped Keys (Write Buffer)
# =============================================================================


def pending_changes_key(execution_id: str) -> str:
    """
    Key for the hash containing pending changes for an execution.

    Structure: HASH where field = change identifier, value = JSON change record
    Used by write buffer, cleared after flush.
    """
    return f"bifrost:pending:{execution_id}"


def execution_logs_stream_key(execution_id: str) -> str:
    """
    Key for the Redis Stream containing logs for an execution.

    Structure: STREAM with log entries
    """
    return f"bifrost:logs:{execution_id}"


def agent_run_steps_stream_key(run_id: str) -> str:
    """
    Key for the Redis Stream containing steps for an agent run.

    Structure: STREAM with step entries (id, run_id, step_number, type, content, etc.)
    Used for dual-read: API reads from Redis when run is in-progress, DB when complete.
    """
    return f"bifrost:agent_run_steps:{run_id}"


# =============================================================================
# Authentication Keys (Refresh Token JTI, OAuth State, Rate Limiting)
# =============================================================================


def refresh_token_jti_key(user_id: str, jti: str) -> str:
    """
    Key for an active refresh token JTI.

    Structure: STRING with value "1" (existence check only)
    TTL: 7 days (matches refresh token expiry)
    """
    return f"bifrost:auth:refresh:{user_id}:{jti}"


def user_refresh_tokens_pattern(user_id: str) -> str:
    """
    Pattern to find all refresh tokens for a user (for revoke-all).

    Use with KEYS or SCAN command.
    """
    return f"bifrost:auth:refresh:{user_id}:*"


def oauth_state_key(state: str) -> str:
    """
    Key for OAuth state with bound PKCE verifier.

    Structure: STRING containing the code_verifier
    TTL: 10 minutes
    """
    return f"bifrost:auth:oauth_state:{state}"


def rate_limit_key(endpoint: str, identifier: str) -> str:
    """
    Key for rate limiting by endpoint and IP/user.

    Structure: STRING with request count
    TTL: 60 seconds (sliding window)
    """
    return f"bifrost:ratelimit:{endpoint}:{identifier}"


def device_code_key(device_code: str) -> str:
    """
    Key for device authorization code storage.

    Structure: JSON with {"user_code": str, "status": str, "user_id": str | null}
    TTL: 5 minutes
    """
    return f"bifrost:auth:device:{device_code}"


def device_user_code_index_key(user_code: str) -> str:
    """
    Reverse index from user_code to device_code.

    Structure: STRING containing device_code
    TTL: 5 minutes
    """
    return f"bifrost:auth:device_user_code:{user_code}"


# =============================================================================
# TTL Constants
# =============================================================================


# TTLs in seconds
TTL_CONFIG = 300  # 5 minutes
TTL_FORMS = 600  # 10 minutes
TTL_ROLES = 600  # 10 minutes
TTL_ORGS = 3600  # 1 hour
TTL_PENDING = 3600  # 1 hour (safety for orphaned changes)
TTL_PENDING_EXECUTION = 3600  # 1 hour (safety for orphaned pending executions)

# Embed TTLs
TTL_EMBED_EXECUTION = 86400  # 24 hours (embed session → execution link)

# Auth TTLs
TTL_REFRESH_TOKEN = 604800  # 7 days (matches refresh token expiry)
TTL_OAUTH_STATE = 600  # 10 minutes
TTL_RATE_LIMIT = 60  # 1 minute window
TTL_DEVICE_CODE = 300  # 5 minutes (device authorization flow)
