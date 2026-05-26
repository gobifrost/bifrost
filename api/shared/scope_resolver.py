"""
Effective-scope resolution for Bifrost.

ONE function. ONE rule table. Used by every entry point that needs to decide
"what org_id should we operate against, given who is calling and what they
asked for?"

See `api/src/repositories/README.md` for the full pattern, including how this
function interacts with `OrgScopedRepository` and the cascade primitive.

The trust model:
    - The engine sentinel authenticates to the API as a single fixed superuser
      identity and resolves scope SDK-side using this function.
    - Direct user-facing endpoints (REST hit by the UI) apply this function
      against the authenticated user; principal IS caller.
    - MCP authenticates as the user directly and does not go through this
      pattern.

If the engine sentinel credential leaks, this entire isolation model
collapses. That is a known and accepted cost of the architecture.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID


class ScopeNotAllowed(Exception):
    """Raised when a caller requests a scope they are not authorized to use.

    The four rules:

      | requested_scope         | allowed if...         | result
      | ----------------------- | --------------------- | ----------------
      | UNSET (default)         | always                | caller_org_id
      | None (explicit global)  | is_platform_admin     | None
      | caller_org_id           | always                | caller_org_id
      | any other UUID          | is_platform_admin     | that UUID

    Any other case raises this exception. It must never be silently coerced
    into a permitted scope — that's the bug class this function exists to
    prevent.
    """


# Sentinel for "the caller did not specify a scope; use their default."
# Distinct from `None`, which means "I am explicitly asking for the global
# scope." Today's `_get_cli_org_id` collapses these two and that's a bug —
# `None` in a request body is meaningful and only platform admins can pass it.
class _Unset:
    """Sentinel type for unspecified scope."""

    _instance: "_Unset | None" = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET: Final[_Unset] = _Unset()

# The type a caller can pass as `requested_scope`. UNSET = unspecified;
# None = explicit global; UUID = a specific org.
RequestedScope = _Unset | None | UUID


def resolve_effective_scope(
    *,
    caller_org_id: UUID | None,
    is_platform_admin: bool,
    requested_scope: RequestedScope = UNSET,
) -> UUID | None:
    """Resolve the effective org scope for an operation.

    Args:
        caller_org_id: The originating caller's organization. None if the
            caller has no org (platform admins may legitimately have no org).
        is_platform_admin: Whether the caller is a platform admin. Platform
            admins can request any org's scope or global; everyone else is
            limited to their own org.
        requested_scope: What the caller asked for.
            - UNSET (default): no explicit request, use caller's default org.
            - None: explicit request for global scope. Platform admins only.
            - UUID: a specific org. Allowed if it matches caller_org_id, or
              if the caller is a platform admin.

    Returns:
        The org UUID to operate against, or None for global.

    Raises:
        ScopeNotAllowed: If the caller is not authorized to use the requested
            scope. The message identifies the rule that failed without
            disclosing other orgs' existence.
    """
    if isinstance(requested_scope, _Unset):
        return caller_org_id

    if requested_scope is None:
        if not is_platform_admin:
            raise ScopeNotAllowed(
                "Explicit global scope requested; platform admin required"
            )
        return None

    if requested_scope == caller_org_id:
        return requested_scope

    if not is_platform_admin:
        raise ScopeNotAllowed(
            "Requested scope is not the caller's organization; "
            "platform admin required"
        )
    return requested_scope
