"""
Redis key generation functions for Bifrost cache.

All Redis keys follow the pattern: bifrost:{scope}:{entity}:{id}
Where scope is either "global" or "org:{org_uuid}"

These functions are the SINGLE SOURCE OF TRUTH for key generation.
Used by both SDK (reads) and API routes (invalidation).
"""

from __future__ import annotations

import json


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
    Base key for a complete config snapshot hash.

    The versioned reader key adds global and (for an org) local generations
    plus the ``v2`` format suffix. A v2 hash has an unquoted ``complete``
    metadata field and JSON-string-encoded user-key fields, each containing
    JSON config data.

    NOTE: Org-scoped config caches are merged views (global fallback
    overlaid with org-specific values). To invalidate every org cache
    efficiently on a global config write — without enumerating org
    keys via SCAN — the org-scoped key embeds a version suffix that
    advances when ``CONFIG_GLOBAL_VERSION_KEY`` is bumped.

    The lookup for the current version is async, so readers must use
    ``config_hash_key_versioned``. This helper is only the stable base used to
    derive a versioned snapshot key.
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


def config_scope_version_key(org_id: str) -> str:
    """Version counter for one organization's merged config view.

    A keyed config read can finish its database query after an org write has
    committed.  Advancing this counter makes the old fill unreachable instead
    of relying on a delete racing that reader.
    """
    return f"bifrost:org:{org_id}:config_version"


CONFIG_CACHE_COMPLETE_FIELD = "complete"


def config_cache_field(key: str) -> str:
    """Encode a user config key into the cache hash's data namespace.

    Every user field is a JSON string (and therefore starts with ``\"``),
    while metadata uses fixed unquoted fields such as ``complete``. The two
    namespaces cannot collide, including for direct database imports that
    bypass request-model key validation.
    """
    return json.dumps(key)


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

    Every view embeds its current invalidation generation.  Org-scoped keys
    include both the global fallback generation and their own local generation.
    This lets a reader safely fill only the version it observed before its SQL
    query: a concurrent global or org mutation advances the next reader to a
    different key.

    Reader and writer must use this helper so a global write naturally
    causes every org read to see a fresh cache key (and thus a DB
    re-merge on next read).
    """
    base = config_hash_key(org_id)
    global_version = await _current_global_version(redis_client)
    if org_id is None or org_id == "GLOBAL":
        return f"{base}:v{global_version}:v2"

    raw_org_version = await redis_client.get(config_scope_version_key(org_id))
    if isinstance(raw_org_version, bytes):
        raw_org_version = raw_org_version.decode()
    try:
        org_version = int(raw_org_version) if raw_org_version is not None else 0
    except (TypeError, ValueError):
        org_version = 0
    return f"{base}:g{global_version}:o{org_version}:v2"


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


def browser_impersonation_session_key(session_id: str) -> str:
    """Key for one short-lived, bearer-only browser impersonation session."""
    return f"bifrost:auth:browser-impersonation:{session_id}"


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


def active_execution_key(execution_id: str) -> str:
    """Compact parent-owned lease for an actively running execution."""
    return f"bifrost:exec:{execution_id}:active"


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


def execution_logs_stream_key(execution_id: str) -> str:
    """
    Key for the Redis Stream containing logs for an execution.

    Structure: STREAM with log entries
    """
    return f"bifrost:logs:{execution_id}"


def execution_log_recovery_cursor_key() -> str:
    """Cursor for bounded scans of retained execution-log streams."""
    return "bifrost:execution-log-recovery:cursor"


def execution_log_retention_cursor_key() -> str:
    """Cursor for bounded orphaned execution-log stream reclamation."""
    return "bifrost:execution-log-retention:cursor"


def service_logs_stream_key(attempt_id: str) -> str:
    """
    Key for the Redis Stream containing logs for one service attempt.

    Attempt-scoped by construction: a stale (fenced) attempt keeps appending
    to its own superseded stream, which no reader follows after reassignment.
    The service-wide timeline (Slice 4) reads the live attempt's stream.
    """
    return f"bifrost:service-logs:{attempt_id}"


def service_logs_cursor_key(attempt_id: str) -> str:
    """
    Last Postgres-flushed stream entry ID for one service attempt.

    Owned by whoever owns the attempt: the claim-loop flush advances it,
    and a new owner resumes from it — so failover never duplicates rows.
    Plain string key (not a hash field); deleted when the attempt completes.
    """
    return f"bifrost:service-logs:{attempt_id}:cursor"


def service_token_key(attempt_id: str) -> str:
    """Parent-rotated service credential handoff (JSON {token, expires_at})."""
    return f"bifrost:service:{attempt_id}:token"


def service_ready_key(attempt_id: str) -> str:
    """Child-reported readiness flag, consumed by the owning worker."""
    return f"bifrost:service:{attempt_id}:ready"


def service_stop_key(attempt_id: str) -> str:
    """Parent-mirrored stop request, polled by the child supervisor."""
    return f"bifrost:service:{attempt_id}:stop"


def chat_run_events_stream_key(conversation_id: str) -> str:
    """Redis Stream containing replayable chat run events for a conversation."""
    return f"bifrost:chat:{conversation_id}:events"


def chat_run_events_version_key(conversation_id: str) -> str:
    """Monotonic sequence key for chat run events in a conversation."""
    return f"bifrost:chat:{conversation_id}:sequence"


def agent_run_steps_stream_key(run_id: str) -> str:
    """
    Key for the Redis Stream containing steps for an agent run.

    Structure: STREAM with step entries (id, run_id, step_number, type, content, etc.)
    Used for dual-read: API reads from Redis when run is in-progress, DB when complete.
    """
    return f"bifrost:agent_run_steps:{run_id}"


# =============================================================================
# Authentication Keys (OAuth State, Rate Limiting)
# =============================================================================


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
TTL_PENDING_EXECUTION = 3600  # 1 hour (safety for orphaned pending executions)
TTL_ACTIVE_EXECUTION = 3600  # 1 hour (recreated while the child is active)
TTL_EXECUTION_LOGS = 604800  # 7 days (durable DB copy is authoritative after flush)

# Embed TTLs
TTL_EMBED_EXECUTION = 86400  # 24 hours (embed session → execution link)

# Auth TTLs
TTL_OAUTH_STATE = 600  # 10 minutes
TTL_RATE_LIMIT = 60  # 1 minute window
TTL_DEVICE_CODE = 300  # 5 minutes (device authorization flow)
