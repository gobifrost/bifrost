"""Portable-bundle scrub rules for ``bifrost export --portable``.

Pure functions that take a parsed manifest dictionary (the result of loading
every ``.bifrost/*.yaml`` file with :func:`yaml.safe_load`) and return a
scrubbed copy plus a human-readable summary of the rules that fired.

The scrub is environment-agnostic: it strips organization UUIDs, user
attribution, timestamps, OAuth secrets, secret-config values, and adapter
runtime state from event sources. Role UUIDs on forms / agents / apps are
rewritten to role *names* (via the caller-supplied ``role_names_by_id`` map)
so the bundle can be re-hydrated into a target environment that uses
different role UUIDs.

UUIDs of the entities themselves (``id`` on workflows, forms, etc.) are
**preserved** — this keeps round-trip export/import into the *same*
environment idempotent (the importer upserts by ID).

Never imports anything from :mod:`api.src` — the scrub is a CLI-side
transformation over the already-serialized manifest returned by
``GET /api/files/manifest``.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Field classifications
# ---------------------------------------------------------------------------

# Fields stripped from *every* entity dict, regardless of entity type.
_ATTRIBUTION_FIELDS: frozenset[str] = frozenset({
    "user_id",
    "created_by",
    "updated_by",
})

# Timestamp fields — exact match plus the ``last_*`` prefix catch-all.
_TIMESTAMP_FIELDS: frozenset[str] = frozenset({
    "created_at",
    "updated_at",
    "deleted_at",
})

# OAuth secrets — stripped anywhere they appear.
_OAUTH_SECRETS: frozenset[str] = frozenset({
    "client_secret",
    "oauth_token_id",
    "access_token",
    "refresh_token",
})

# Event-source adapter runtime state — adapter-managed, not portable.
_EVENT_SOURCE_RUNTIME_FIELDS: frozenset[str] = frozenset({
    "external_id",
    "expires_at",
    "state",
})

# Top-level manifest sections that hold role-id lists on each entity (top-level ``roles`` field).
_ROLE_ID_SECTIONS: tuple[str, ...] = ("forms", "agents", "apps")


def _is_timestamp_key(key: str) -> bool:
    """Match explicit timestamp field names and the ``last_*`` prefix."""
    if key in _TIMESTAMP_FIELDS:
        return True
    return key.startswith("last_")


def _iter_entity_dicts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a flat list of every entity-dict inside the manifest.

    The manifest uses two shapes:
    * ``list[dict]`` for ``organizations`` / ``roles``.
    * ``dict[str, dict]`` (keyed by UUID) for every other section.

    We normalise both into a flat list so the scrub loop is uniform.
    """
    entities: list[dict[str, Any]] = []
    for section_value in manifest.values():
        if isinstance(section_value, list):
            entities.extend(item for item in section_value if isinstance(item, dict))
        elif isinstance(section_value, dict):
            entities.extend(
                item for item in section_value.values() if isinstance(item, dict)
            )
    return entities


# ---------------------------------------------------------------------------
# Scrub pipeline
# ---------------------------------------------------------------------------


def _strip_org_ids(manifest: dict[str, Any]) -> int:
    """Strip ``organization_id`` from every dict in the tree.

    Walks recursively because nested dicts (e.g. integration mappings)
    also carry ``organization_id`` that must not leak into a portable
    bundle. Returns the total count removed.
    """
    removed = 0

    def _walk(value: Any) -> None:
        nonlocal removed
        if isinstance(value, dict):
            if "organization_id" in value:
                del value["organization_id"]
                removed += 1
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(manifest)
    return removed


def _strip_attribution(manifest: dict[str, Any]) -> int:
    """Strip user_id / created_by / updated_by from every entity."""
    removed = 0
    for entity in _iter_entity_dicts(manifest):
        for field in list(entity.keys()):
            if field in _ATTRIBUTION_FIELDS:
                del entity[field]
                removed += 1
    return removed


def _strip_timestamps(manifest: dict[str, Any]) -> int:
    """Strip created_at / updated_at / deleted_at / last_* from every entity."""
    removed = 0
    for entity in _iter_entity_dicts(manifest):
        for field in list(entity.keys()):
            if _is_timestamp_key(field):
                del entity[field]
                removed += 1
    return removed


def _strip_oauth_secrets(manifest: dict[str, Any]) -> int:
    """Strip OAuth secret fields anywhere they appear in the manifest."""
    removed = 0

    def _walk(value: Any) -> None:
        nonlocal removed
        if isinstance(value, dict):
            for key in list(value.keys()):
                if key in _OAUTH_SECRETS:
                    del value[key]
                    removed += 1
                    continue
                _walk(value[key])
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(manifest)
    return removed


def _null_secret_config_values(manifest: dict[str, Any]) -> int:
    """Replace ``value`` with ``None`` on ``config_type == "secret"`` configs.

    Operates over ``manifest["configs"]`` (a dict keyed by UUID). The
    ``description`` field is preserved untouched. Non-secret configs are
    left alone.
    """
    configs = manifest.get("configs")
    if not isinstance(configs, dict):
        return 0

    nulled = 0
    for config in configs.values():
        if not isinstance(config, dict):
            continue
        if config.get("config_type") == "secret" and "value" in config:
            config["value"] = None
            nulled += 1
    return nulled


def _strip_event_source_runtime_state(manifest: dict[str, Any]) -> int:
    """Strip adapter-managed runtime state from event sources."""
    events = manifest.get("events")
    if not isinstance(events, dict):
        return 0

    removed = 0
    for event in events.values():
        if not isinstance(event, dict):
            continue
        for field in list(event.keys()):
            if field in _EVENT_SOURCE_RUNTIME_FIELDS:
                del event[field]
                removed += 1
    return removed


def _rewrite_role_ids_to_names(
    manifest: dict[str, Any],
    role_names_by_id: dict[str, str],
) -> dict[str, int]:
    """Replace ``roles: [<uuid>, ...]`` with ``role_names: [<name>, ...]``.

    UUIDs that don't appear in ``role_names_by_id`` are kept as-is under a
    separate ``unresolved_role_ids`` key so the importer can surface them
    rather than silently dropping the binding.

    Returns a per-section count of entities that had role IDs rewritten.

    Note: the parallel rewrite for ``has_role`` arguments inside table policy
    ASTs lives in :func:`_rewrite_has_role_in_table_policies`. The two are
    structurally distinct (list-of-UUIDs vs. inline AST literal) so they're
    serialized using different markers — bare names in a separate field for
    role lists, ``@<name>`` prefix for inline AST args.
    """
    counts: dict[str, int] = {}
    for section in _ROLE_ID_SECTIONS:
        section_value = manifest.get(section)
        if not isinstance(section_value, dict):
            continue
        rewritten = 0
        for entity in section_value.values():
            if not isinstance(entity, dict):
                continue
            role_ids = entity.get("roles")
            if not isinstance(role_ids, list) or not role_ids:
                continue
            names: list[str] = []
            unresolved: list[str] = []
            for role_id in role_ids:
                if not isinstance(role_id, str):
                    continue
                name = role_names_by_id.get(role_id)
                if name is None:
                    unresolved.append(role_id)
                else:
                    names.append(name)
            entity["role_names"] = names
            del entity["roles"]
            if unresolved:
                entity["unresolved_role_ids"] = unresolved
            rewritten += 1
        if rewritten:
            counts[section] = rewritten

    return counts


def _rewrite_has_role_in_table_policies(
    manifest: dict[str, Any],
    role_names_by_id: dict[str, str],
) -> int:
    """Walk every table policy AST and rewrite ``has_role`` UUID args to ``@<name>``.

    Returns the number of policies (across all tables) whose ``when`` AST was
    visited. This is used for the rules_applied summary; not the count of
    actual UUID rewrites because a single policy may contain multiple
    ``has_role`` calls or none at all.
    """
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        return 0
    visited = 0
    for table in tables.values():
        if not isinstance(table, dict):
            continue
        policy_list = table.get("policies")
        if not isinstance(policy_list, list):
            continue
        for policy in policy_list:
            if not isinstance(policy, dict):
                continue
            when = policy.get("when")
            if when is None:
                continue
            policy["when"] = _rewrite_has_role_in_expr(when, role_names_by_id)
            visited += 1
    return visited


def _rewrite_has_role_in_expr(
    node: Any,
    role_names_by_id: dict[str, str],
) -> Any:
    """Recursively rewrite ``has_role`` UUID args to ``@<name>`` markers.

    Walks the policy AST in place-style (returning a new dict at each level so
    callers don't accidentally observe partial rewrites). ``has_role`` calls may
    appear at any depth — wrapped in ``and`` / ``or`` / ``not``, nested under
    comparisons, etc. — so we recurse through every dict and list value.

    UUIDs that don't appear in ``role_names_by_id`` are left untouched so the
    inverse rewriter can either match them (if the bundle is being imported
    into the same env) or surface them as unresolved.
    """
    if isinstance(node, dict):
        if node.get("call") == "has_role":
            args = node.get("args", [])
            new_args: list[Any] = []
            for arg in args:
                if isinstance(arg, str):
                    name = role_names_by_id.get(arg)
                    new_args.append(f"@{name}" if name else arg)
                else:
                    new_args.append(_rewrite_has_role_in_expr(arg, role_names_by_id))
            return {**node, "args": new_args}
        return {k: _rewrite_has_role_in_expr(v, role_names_by_id) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite_has_role_in_expr(item, role_names_by_id) for item in node]
    return node


def _rewrite_role_names_to_ids(
    manifest: dict[str, Any],
    role_ids_by_name: dict[str, str],
) -> dict[str, Any]:
    """Inverse of :func:`_rewrite_role_ids_to_names` for ``has_role`` AST args.

    Walks every ``tables[*].policies[*].when`` AST and rewrites
    ``"@<name>"`` markers back to the role UUID against the target environment's
    role table. Names that don't resolve are left as-is (still prefixed) so the
    server-side importer can fail loud rather than silently swap in a wrong UUID.

    Mutates ``manifest`` in place and also returns it for convenience.
    """
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        return manifest
    for table in tables.values():
        if not isinstance(table, dict):
            continue
        policy_list = table.get("policies")
        if not isinstance(policy_list, list):
            continue
        for policy in policy_list:
            if not isinstance(policy, dict):
                continue
            when = policy.get("when")
            if when is None:
                continue
            policy["when"] = _restore_has_role_in_expr(when, role_ids_by_name)
    return manifest


def _restore_has_role_in_expr(
    node: Any,
    role_ids_by_name: dict[str, str],
) -> Any:
    """Inverse of :func:`_rewrite_has_role_in_expr`."""
    if isinstance(node, dict):
        if node.get("call") == "has_role":
            args = node.get("args", [])
            new_args: list[Any] = []
            for arg in args:
                if isinstance(arg, str) and arg.startswith("@"):
                    name = arg[1:]
                    role_id = role_ids_by_name.get(name)
                    new_args.append(role_id if role_id else arg)
                else:
                    new_args.append(_restore_has_role_in_expr(arg, role_ids_by_name))
            return {**node, "args": new_args}
        return {k: _restore_has_role_in_expr(v, role_ids_by_name) for k, v in node.items()}
    if isinstance(node, list):
        return [_restore_has_role_in_expr(item, role_ids_by_name) for item in node]
    return node


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scrub(
    manifest: dict[str, Any],
    *,
    role_names_by_id: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """Scrub a manifest dict for portable (community-shareable) export.

    Args:
        manifest: Parsed manifest dict. Top-level keys match
            :data:`bifrost.manifest.MANIFEST_FILES` (``workflows``,
            ``integrations``, ``forms``, ``agents``, ``apps``, ``configs``,
            ``tables``, ``events``, ``organizations``, ``roles``). Each
            value is either a ``list[dict]`` (orgs, roles) or a
            ``dict[str, dict]`` keyed by entity UUID.
        role_names_by_id: Map of role UUID → role name, typically built
            from ``GET /api/roles``. Used to translate ``roles`` UUID
            lists on forms/agents/apps into human-readable names.

    Returns:
        Tuple of ``(scrubbed_manifest, rules_applied)`` where
        ``rules_applied`` is a list of descriptions suitable for inclusion
        in ``bundle.meta.yaml``.

    The input ``manifest`` is **not** mutated — the function operates on a
    deep copy to stay idempotent from the caller's perspective.
    """
    from copy import deepcopy

    working = deepcopy(manifest)
    rules_applied: list[str] = []

    org_count = _strip_org_ids(working)
    if org_count:
        rules_applied.append(f"stripped {org_count} organization_id field(s)")

    attribution_count = _strip_attribution(working)
    if attribution_count:
        rules_applied.append(
            f"stripped {attribution_count} attribution field(s) "
            "(user_id / created_by / updated_by)"
        )

    timestamp_count = _strip_timestamps(working)
    if timestamp_count:
        rules_applied.append(
            f"stripped {timestamp_count} timestamp field(s) "
            "(created_at / updated_at / deleted_at / last_*)"
        )

    oauth_count = _strip_oauth_secrets(working)
    if oauth_count:
        rules_applied.append(
            f"stripped {oauth_count} OAuth secret field(s) "
            "(client_secret / oauth_token_id / access_token / refresh_token)"
        )

    secret_configs = _null_secret_config_values(working)
    if secret_configs:
        rules_applied.append(
            f"nulled {secret_configs} secret-type config value(s)"
        )

    event_runtime = _strip_event_source_runtime_state(working)
    if event_runtime:
        rules_applied.append(
            f"stripped {event_runtime} event-source runtime field(s) "
            "(external_id / expires_at / state)"
        )

    role_counts = _rewrite_role_ids_to_names(working, role_names_by_id)
    for section, count in role_counts.items():
        rules_applied.append(
            f"rewrote {count} role_ids -> role_names on {section}"
        )

    has_role_visited = _rewrite_has_role_in_table_policies(working, role_names_by_id)
    if has_role_visited:
        rules_applied.append(
            f"rewrote has_role role IDs to @-names in {has_role_visited} table policy expression(s)"
        )

    return working, rules_applied


__all__ = ["scrub"]
