"""Persona x operation matrix: the R2c evaluator against today's behaviour.

For every access-list entry, every persona and every target, the evaluator
must decide what today's code decides (``legacy_oracle``), except:

- the listed difference: a provider-org member on an entry that records an
  ``intended_change`` (the provider-org non-admin path goes away at R3), and
- ``PENDING_DECISION``: cells whose difference is a who-can-do-what question
  awaiting a decision, listed explicitly so the rest of the matrix stays
  strict.
"""

from __future__ import annotations

from collections import defaultdict

from src.models.contracts.access_list import AccessEntry
from src.services.access_list import ACCESS_LIST
from src.services.authorization.evaluator import GLOBAL, HOME, Target, cross_org, decide
from src.services.operation_catalog import OPERATION_CATALOG
from tests.unit.authorization.legacy_oracle import (
    OTHER_CUSTOMER_ORG_ID,
    PERSONAS,
    Persona,
    legacy_decide,
)

TARGETS: tuple[tuple[str, Target, bool], ...] = (
    ("home", HOME, False),
    ("cross_org", cross_org(OTHER_CUSTOMER_ORG_ID), True),
    ("cross_global", GLOBAL, True),
)

_REST_ENTRIES = {e.key: e for e in ACCESS_LIST if e.mcp_tool is None}
_BOUND_REST_KEY = {
    op.mcp.name: (op.rest.method, op.rest.path)
    for op in OPERATION_CATALOG
    if op.mcp and op.rest
}

# Read permissions the User base role holds (derived in R2b from the access
# list) that ALSO gate operations today's code restricts to admins or
# provider-org members: a derivation counts an MCP tool's transport-floor
# gate, or an entry narrowed by an inline check, as plain "authenticated". On
# such an operation the new model would let every signed-in user in where
# today's code refuses. Pending decision; each permission must still produce
# such a cell (`test_pending_decision_still_differs`).
USER_BASE_OVERGRANT: frozenset[str] = frozenset(
    {
        "agentruns.read",
        "apps.read",
        "configs.read",
        "events.read",
        "forms.read",
        "integrations.read",
        "mcp.read",
        "metrics.read",
        "policyrules.read",
        "roles.read",
        "settings.read",
        "tables.read",
        "workflows.read",
    }
)

_NON_ADMINS = frozenset({"provider_member", "regular", "external"})
_CUSTOMERS = frozenset({"regular", "external"})
_PROVIDER = frozenset({"provider_member"})

# Other cells whose difference is a who-can-do-what question awaiting a
# decision: entry key -> personas excused (at every target).
PENDING_DECISION: dict[tuple[str, str] | str, frozenset[str]] = {
    # Own-run writes classed as a platform permission the User role lacks.
    ("POST", "/api/agent-runs/{run_id}/flag-conversation/message"): _CUSTOMERS,
    ("POST", "/api/agent-runs/{run_id}/verdict"): _CUSTOMERS,
    ("DELETE", "/api/agent-runs/{run_id}/verdict"): _CUSTOMERS,
    # Agent discovery open to every signed-in user, classed as mcp.readwrite.
    ("POST", "/api/mcp/gateway/capabilities/search"): _NON_ADMINS,
    # Per-user event channel open to every signed-in user, classed platform.read.
    ("WS", "/ws/connect"): _CUSTOMERS,
    # Class personal on a superuser-gated route: today only superusers.
    ("POST", "/api/oauth/callback/{connection_name}"): _NON_ADMINS,
    ("POST", "/api/oauth/connections"): _NON_ADMINS,
    ("DELETE", "/api/oauth/connections/{connection_name}"): _NON_ADMINS,
    ("GET", "/api/oauth/connections/{connection_name}"): _NON_ADMINS,
    ("PUT", "/api/oauth/connections/{connection_name}"): _NON_ADMINS,
    ("POST", "/api/oauth/connections/{connection_name}/authorize"): _NON_ADMINS,
    ("POST", "/api/oauth/connections/{connection_name}/cancel"): _NON_ADMINS,
    ("POST", "/api/oauth/connections/{connection_name}/refresh"): _NON_ADMINS,
    ("GET", "/api/oauth/credentials/{connection_name}"): _NON_ADMINS,
    # Class public on a tool the MCP transport only serves to signed-in callers.
    "get_docs": frozenset({"anonymous"}),
    # Provider-org scope bypass reached through a service the one-hop token
    # scan does not see, so the entry carries no intended_change marker.
    ("POST", "/api/mcp/gateway/agents/{agent_id}/tools/{tool_ref}/execute"): _PROVIDER,
    ("GET", "/api/mcp/gateway/executions/{execution_id}"): _PROVIDER,
    ("GET", "/api/mcp/status"): _PROVIDER,
    ("POST", "/api/sdk/ai/complete"): _PROVIDER,
    ("POST", "/api/sdk/ai/stream"): _PROVIDER,
    ("GET", "/api/sdk/context"): _PROVIDER,
    ("GET", "/api/sdk/knowledge/get"): _PROVIDER,
    ("GET", "/api/sdk/knowledge/namespaces"): _PROVIDER,
    ("POST", "/api/sdk/knowledge/search"): _PROVIDER,
}


def effective_entry(entry: AccessEntry) -> AccessEntry:
    """The entry that decides ``entry``: a tool bound to a REST route through
    the operation catalog is decided by that route's entry."""
    if entry.mcp_tool is None:
        return entry
    rest_key = _BOUND_REST_KEY.get(entry.mcp_tool)
    return _REST_ENTRIES[rest_key] if rest_key else entry


def _label(entry: AccessEntry) -> str:
    return entry.mcp_tool or f"{entry.method} {entry.path}"


def _cells():
    for entry in ACCESS_LIST:
        decider = effective_entry(entry)
        for persona in PERSONAS:
            for target_name, target, cross in TARGETS:
                legacy = legacy_decide(persona, decider, cross)
                decision = decide(persona.ctx, decider, target)
                yield entry, decider, persona, target_name, legacy, decision


def _is_pending(entry: AccessEntry, persona: Persona, legacy: bool, decision) -> bool:
    if persona.name in PENDING_DECISION.get(entry.key, frozenset()):
        return True
    return (
        not legacy
        and decision.allowed
        and decision.rule.startswith("base_role:")
        and decision.rule.removeprefix("base_role:") in USER_BASE_OVERGRANT
    )


def _is_listed_difference(decider: AccessEntry, persona: Persona, legacy: bool, allowed: bool) -> bool:
    return persona.name == "provider_member" and decider.intended_change is not None and legacy and not allowed


def _row(entry: AccessEntry, persona: Persona, target_name: str, legacy: bool, decision) -> str:
    return f"{persona.name} / {_label(entry)} / {target_name} / legacy={legacy} / new={decision.allowed} / {decision.rule}"


def test_evaluator_matches_legacy_except_listed_differences() -> None:
    mismatches = []
    for entry, decider, persona, target_name, legacy, decision in _cells():
        if legacy == decision.allowed:
            continue
        if _is_listed_difference(decider, persona, legacy, decision.allowed):
            continue
        if _is_pending(entry, persona, legacy, decision):
            continue
        mismatches.append(_row(entry, persona, target_name, legacy, decision))
    assert not mismatches, f"{len(mismatches)} unexpected differences:\n" + "\n".join(mismatches)


def test_every_intended_change_marker_produces_a_difference() -> None:
    differing: dict = defaultdict(bool)
    for entry, decider, persona, _, legacy, decision in _cells():
        if _is_listed_difference(decider, persona, legacy, decision.allowed):
            differing[entry.key] = True
    stale = [_label(e) for e in ACCESS_LIST if effective_entry(e).intended_change and not differing[e.key]]
    assert not stale, f"intended_change set but no difference produced: {stale}"


def test_pending_decision_entries_still_differ() -> None:
    differing: dict = defaultdict(set)
    for entry, decider, persona, _, legacy, decision in _cells():
        if legacy != decision.allowed and not _is_listed_difference(decider, persona, legacy, decision.allowed):
            differing[entry.key].add(persona.name)
    stale = [
        (key, sorted(personas - differing[key]))
        for key, personas in PENDING_DECISION.items()
        if personas - differing[key]
    ]
    assert not stale, f"pending-decision personas that no longer differ: {stale}"


def test_every_user_base_overgrant_permission_still_produces_a_difference() -> None:
    seen: set[str] = set()
    for _, _, _, _, legacy, decision in _cells():
        if not legacy and decision.allowed and decision.rule.startswith("base_role:"):
            seen.add(decision.rule.removeprefix("base_role:"))
    stale = sorted(USER_BASE_OVERGRANT - seen)
    assert not stale, f"USER_BASE_OVERGRANT permissions with no differing cell: {stale}"
    unlisted = sorted(seen - USER_BASE_OVERGRANT)
    assert not unlisted, f"base-role grants where today's code refuses, not listed: {unlisted}"
