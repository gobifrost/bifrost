"""
Portable reference resolution for the Bifrost CLI and MCP parity tools.

A single client-side helper that maps ``(kind, user-supplied ref)`` to a UUID.
Used by every CLI ``update`` / ``delete`` / sub-resource command and by MCP
parity tools that accept user-friendly refs.

Accepted ref shapes (by kind):

- **UUID** — pass-through; validated and returned immediately.
- **Name** — resolved via the entity's list endpoint.
- **path::func** — ``workflow`` only; matched by
  ``function_name`` + ``source_file_path``.
- **slug** — ``app`` only; resolved via
  ``GET /api/applications/{slug}`` directly.

Config is keyed by ``key`` (the stored column name), not by a ``name`` field —
callers pass the config key as the ``value`` for ``kind="config"``.

Ambiguous name matches raise :class:`AmbiguousRefError` with the full candidate
list so the CLI can tell the user to pass the UUID directly. There is no
``--org`` disambiguation flag by design.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

RefKind = Literal[
    "org",
    "role",
    "workflow",
    "form",
    "agent",
    "app",
    "integration",
    "table",
    "event_source",
    "config",
]


class RefResolutionError(Exception):
    """Base class for ref resolution errors."""


class RefNotFoundError(RefResolutionError):
    """Raised when no entity matches the supplied ref."""

    def __init__(self, kind: str, value: str) -> None:
        self.kind = kind
        self.value = value
        super().__init__(f"No {kind} found matching {value!r}")


class AmbiguousRefError(RefResolutionError):
    """Raised when multiple entities match the supplied ref."""

    def __init__(self, kind: str, value: str, candidates: list[dict[str, Any]]) -> None:
        self.kind = kind
        self.value = value
        # Each candidate is {"name": str, "uuid": str, "org_id": str | None}.
        self.candidates = candidates
        super().__init__(
            f"multiple {kind} entities match {value!r}; pass the UUID instead."
        )


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _candidate(name: str, uuid: str, org_id: str | None) -> dict[str, Any]:
    return {"name": name, "uuid": uuid, "org_id": org_id}


async def _get_json(client: Any, path: str, **kwargs: Any) -> Any:
    """Issue a GET and return parsed JSON; raise on HTTP errors."""
    response = await client.get(path, **kwargs)
    response.raise_for_status()
    return response.json()


async def _resolve_org(client: Any, value: str) -> tuple[str, list[dict[str, Any]]]:
    items = await _get_json(client, "/api/organizations")
    matches = [o for o in items if o.get("name") == value]
    candidates = [_candidate(o["name"], str(o["id"]), None) for o in matches]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_role(client: Any, value: str) -> tuple[str, list[dict[str, Any]]]:
    items = await _get_json(client, "/api/roles")
    matches = [r for r in items if r.get("name") == value]
    candidates = [_candidate(r["name"], str(r["id"]), None) for r in matches]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_workflow(
    client: Any, value: str
) -> tuple[str, list[dict[str, Any]]]:
    items = await _get_json(client, "/api/workflows")

    path_ref: str | None = None
    func_ref: str | None = None
    if "::" in value:
        path_ref, func_ref = value.split("::", 1)
        if not path_ref or not func_ref:
            # Treat partially-empty "::" splits as a name lookup.
            path_ref = None
            func_ref = None

    def _path_matches(wf: dict[str, Any], target: str) -> bool:
        for field in ("source_file_path", "relative_file_path"):
            candidate_path = wf.get(field)
            if candidate_path and (
                candidate_path == target or candidate_path.endswith("/" + target)
            ):
                return True
        return False

    if path_ref is not None and func_ref is not None:
        matches = [
            w
            for w in items
            if w.get("function_name") == func_ref and _path_matches(w, path_ref)
        ]
    else:
        matches = [w for w in items if w.get("name") == value]

    candidates = [
        _candidate(w.get("name", ""), str(w["id"]), w.get("organization_id"))
        for w in matches
    ]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_form(client: Any, value: str) -> tuple[str, list[dict[str, Any]]]:
    items = await _get_json(client, "/api/forms")
    matches = [f for f in items if f.get("name") == value]
    candidates = [
        _candidate(f["name"], str(f["id"]), _as_opt_str(f.get("organization_id")))
        for f in matches
    ]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_agent(client: Any, value: str) -> tuple[str, list[dict[str, Any]]]:
    items = await _get_json(client, "/api/agents")
    matches = [a for a in items if a.get("name") == value]
    candidates = [
        _candidate(a["name"], str(a["id"]), _as_opt_str(a.get("organization_id")))
        for a in matches
    ]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_app(client: Any, value: str) -> tuple[str, list[dict[str, Any]]]:
    # Try slug first — apps expose ``GET /api/applications/{slug}`` directly.
    slug_response = await client.get(f"/api/applications/{value}")
    if slug_response.status_code == 200:
        app = slug_response.json()
        return str(app["id"]), [
            _candidate(
                app.get("name", value),
                str(app["id"]),
                _as_opt_str(app.get("organization_id")),
            )
        ]
    if slug_response.status_code not in (403, 404):
        slug_response.raise_for_status()

    # Fall through to name match across accessible scopes.
    data = await _get_json(client, "/api/applications")
    items = data.get("applications", []) if isinstance(data, dict) else data
    matches = [a for a in items if a.get("name") == value]
    candidates = [
        _candidate(a["name"], str(a["id"]), _as_opt_str(a.get("organization_id")))
        for a in matches
    ]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_integration(
    client: Any, value: str
) -> tuple[str, list[dict[str, Any]]]:
    data = await _get_json(client, "/api/integrations")
    items = data.get("items", []) if isinstance(data, dict) else data
    matches = [i for i in items if i.get("name") == value]
    candidates = [_candidate(i["name"], str(i["id"]), None) for i in matches]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_table(client: Any, value: str) -> tuple[str, list[dict[str, Any]]]:
    data = await _get_json(client, "/api/tables")
    items = data.get("tables", []) if isinstance(data, dict) else data
    matches = [t for t in items if t.get("name") == value]
    candidates = [
        _candidate(t["name"], str(t["id"]), _as_opt_str(t.get("organization_id")))
        for t in matches
    ]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_event_source(
    client: Any, value: str
) -> tuple[str, list[dict[str, Any]]]:
    data = await _get_json(client, "/api/events/sources")
    items = data.get("items", []) if isinstance(data, dict) else data
    matches = [s for s in items if s.get("name") == value]
    candidates = [
        _candidate(s["name"], str(s["id"]), _as_opt_str(s.get("organization_id")))
        for s in matches
    ]
    if len(matches) == 1:
        return str(matches[0]["id"]), candidates
    return "", candidates


async def _resolve_config(
    client: Any, value: str
) -> tuple[str, list[dict[str, Any]]]:
    # Configs are keyed by ``key`` + ``org_id``; "name" == ``key`` for this helper.
    items = await _get_json(client, "/api/config")
    matches = [c for c in items if c.get("key") == value]
    candidates: list[dict[str, Any]] = []
    for c in matches:
        config_id = c.get("id")
        if not config_id:
            # Config without an id cannot be resolved to a UUID.
            continue
        candidates.append(
            _candidate(c["key"], str(config_id), _as_opt_str(c.get("org_id")))
        )
    if len(candidates) == 1:
        return candidates[0]["uuid"], candidates
    return "", candidates


def _as_opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


_RESOLVERS = {
    "org": _resolve_org,
    "role": _resolve_role,
    "workflow": _resolve_workflow,
    "form": _resolve_form,
    "agent": _resolve_agent,
    "app": _resolve_app,
    "integration": _resolve_integration,
    "table": _resolve_table,
    "event_source": _resolve_event_source,
    "config": _resolve_config,
}


async def resolve_ref(
    client: Any,
    kind: RefKind,
    value: str,
    *,
    cache: dict[tuple[str, str], str] | None = None,
) -> str:
    """Resolve a user-supplied ref to a UUID string.

    Args:
        client: Async HTTP client exposing ``async def get(path) -> Response``
            (e.g. :class:`bifrost.client.BifrostClient`).
        kind: Entity kind.
        value: UUID, name, ``path::func`` (workflow), or slug (app).
        cache: Optional per-invocation cache, keyed by ``(kind, value)``.
            The CLI command instance owns one cache dict per invocation.

    Returns:
        The resolved UUID as a string.

    Raises:
        RefNotFoundError: No entity matches ``value``.
        AmbiguousRefError: Multiple entities match; raises with the full
            candidate list so the caller can tell the user to pass the UUID.
        ValueError: Unknown ``kind``.
    """
    if kind not in _RESOLVERS:
        raise ValueError(f"Unknown ref kind: {kind!r}")

    if _is_uuid(value):
        return str(UUID(value))

    if cache is not None:
        cached = cache.get((kind, value))
        if cached is not None:
            return cached

    resolver = _RESOLVERS[kind]
    resolved, candidates = await resolver(client, value)

    if not resolved:
        if len(candidates) > 1:
            raise AmbiguousRefError(kind, value, candidates)
        raise RefNotFoundError(kind, value)

    if cache is not None:
        cache[(kind, value)] = resolved

    return resolved


class RefResolver:
    """Per-invocation cached resolver bound to an HTTP client.

    Each CLI command instance and each MCP tool invocation creates one
    ``RefResolver`` so a single resolved ``(kind, value)`` pair is reused
    across the request without leaking across invocations.

    Usage::

        resolver = RefResolver(client)
        workflow_uuid = await resolver.resolve("workflow", "MyWorkflow")
    """

    def __init__(
        self,
        client: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = client
        self._headers = dict(headers or {})
        self._cache: dict[tuple[str, str], str] = {}

    async def resolve(self, kind: RefKind, value: str) -> str:
        """Resolve ``value`` to a UUID for the given ``kind``."""
        client = self._client
        if self._headers:
            client = _HeaderBoundClient(client, self._headers)
        return await resolve_ref(client, kind, value, cache=self._cache)


class _HeaderBoundClient:
    """Apply fixed request headers to reference lookup GETs."""

    def __init__(self, client: Any, headers: Mapping[str, str]) -> None:
        self._client = client
        self._headers = dict(headers)

    async def get(self, path: str, **kwargs: Any) -> Any:
        request_headers = dict(kwargs.pop("headers", {}) or {})
        return await self._client.get(
            path,
            **kwargs,
            headers={**self._headers, **request_headers},
        )


__all__ = [
    "AmbiguousRefError",
    "RefKind",
    "RefNotFoundError",
    "RefResolutionError",
    "RefResolver",
    "resolve_ref",
]
